"""Git and GitHub commands (#4). Real git runs against temporary repos with a local bare
remote; `gh` is tests/fake_gh.py, selected by AGENT_VAULT_GH. Nothing here touches GitHub."""
import atexit
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import CODE, REPO, copy_scenario, make_vault, run_py, tmpdir, write_config

sys.path.insert(0, str(CODE))
import github  # noqa: E402
import lib  # noqa: E402

VAULT_PY = CODE / "vault.py"
FAKE_GH = REPO / "tests" / "fake_gh.py"
SLUG = "acme/widget"
TITLE = "feat: add widget frobbing"
CREATED = "2026-09-20T10:00:00Z"
SEALED_PLAN = """---
type: plan
repo: widget
issue: 7
status: draft
---
# widget-7-plan

## Goal
Frob the widgets.

## Acceptance criteria
- AC1: widgets frob

## Decisions

## Implementer's discretion
Naming.

## Context
- `src/a.py`

## Steps
### 1. Frob
Files: `src/a.py`, `src/b.py` (new)
Do: frob
Done when: AC1 holds

## Tests
- T1 → AC1: frobbing works
Full check: make test

## Stop and ask if
- never

## Out of scope
- nothing
"""


NO_PR_PLAN = SEALED_PLAN.replace("status: draft", "pr: none\nstatus: draft").replace(
    "Files: `src/a.py`, `src/b.py` (new)", "Files: none")


ISSUE_BODY = """## Problem
Widgets don't frob.

## Desired outcome
Widgets frob.

## Constraints
None

## Out of scope
Gadgets.

## Source
session
"""


def git(*args, cwd, check=True):
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True).stdout.strip()


class Scenario:
    """GhCase's setup, built once per process: a vault with project `widgets`, a clone whose
    origin points at a local bare remote, and the fake-gh state. Each test gets its own copy."""

    def __init__(self):
        self._t = tmpdir()
        self.root = Path(self._t.name)
        vault = make_vault(self.root)
        cfg = write_config(self.root, vault)
        state_path = self.root / "gh.json"
        bare = self.root / "remote.git"
        clone = self.root / "clone"
        (self.root / "empty-gitconfig").write_text("", encoding="utf-8")
        with mock.patch.dict(os.environ, scenario_env(self.root)):
            git("init", "-q", "--bare", "-b", "main", str(bare), cwd=self.root)
            git("init", "-q", "-b", "main", str(clone), cwd=self.root)
            for k, v in (("user.name", "t"), ("user.email", "t@example.com"), ("commit.gpgsign", "false")):
                git("config", k, v, cwd=clone)
            git("config", f"url.{bare.as_posix()}.insteadOf", f"https://github.com/{SLUG}.git", cwd=clone)
            git("remote", "add", "origin", f"https://github.com/{SLUG}.git", cwd=clone)
            (clone / "README.md").write_text("hi\n", encoding="utf-8")
            git("add", ".", cwd=clone)
            git("commit", "-q", "-m", "init", cwd=clone)
            git("push", "-q", "origin", "main", cwd=clone)
            state = {"repos": {SLUG: {
                "default_branch": "main", "head_sha": git("rev-parse", "HEAD", cwd=clone), "labels": ["bug"],
                "settings": {"allow_squash_merge": True, "allow_merge_commit": True,
                             "allow_rebase_merge": True, "delete_branch_on_merge": False},
                "refuse_settings": False, "branches": [], "prs": [], "bare": str(bare),
                "compare": {"total_commits": 0, "files": []},
                "issues": {"7": {"state": "OPEN", "title": TITLE, "created": CREATED, "body_edited": None,
                                 "renamed": None, "labels": []}},
                "created_prs": [], "created_issues": [], "next_number": 20}}, "calls": []}
            state_path.write_text(json.dumps(state), encoding="utf-8")
            r = run_py(VAULT_PY, ("new", "project", "widgets", "--repo", SLUG), config=cfg, cwd=clone)
            assert r.returncode == 0, r.stdout + r.stderr


def scenario_env(root: Path) -> dict:
    return {"GIT_CONFIG_GLOBAL": str(root / "empty-gitconfig"), "GIT_CONFIG_NOSYSTEM": "1",
            "AGENT_VAULT_GH": str(FAKE_GH), "FAKE_GH_STATE": str(root / "gh.json")}


_scenario = None
_snapshots = {}


def _unlock(func, path, _exc):
    os.chmod(path, 0o700)
    func(path)


class _Snapshot:
    def __init__(self, root):
        self._t = tmpdir()
        atexit.register(self._t.cleanup)
        self.root = Path(self._t.name)
        copy_scenario(root, self.root)


def scenario() -> Scenario:
    global _scenario
    if _scenario is None:
        _scenario = Scenario()
        atexit.register(_scenario._t.cleanup)
    return _scenario


class GhCase(unittest.TestCase):
    """A vault with project `widgets` (repo acme/widget), a clone whose origin looks like the
    GitHub URL but really points at a local bare remote, and a fake gh."""

    def setUp(self):
        self._t = tmpdir()
        self.root = Path(self._t.name)
        copy_scenario(scenario().root, self.root)
        self.vault = self.root / "Notes"
        self.cfg = self.root / "agent-vault.json"
        self.state_path = self.root / "gh.json"
        self.bare = self.root / "remote.git"
        self.clone = self.root / "clone"
        patch = mock.patch.dict(os.environ, scenario_env(self.root))
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self._t.cleanup)
        self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self._pristine = self.fingerprint()

    def fingerprint(self):
        """Names, sizes and times of every file in the test's folder except git's object stores."""
        out = []
        for path in sorted(self.root.rglob("*")):
            parts = path.relative_to(self.root).parts
            if path.is_file() and "objects" not in parts:
                st = path.stat()
                out.append((parts, st.st_size, st.st_mtime_ns))
        return out

    def snapshot(self, key, build):
        """Run `build()`, or, when nothing has changed this test's folder since setUp or the last
        snapshot, copy the folder as an earlier `build()` left it, saved under `key`."""
        saved = _snapshots.get(key)
        clean = self.fingerprint() == self._pristine
        if saved is not None and clean:
            for child in self.root.iterdir():
                shutil.rmtree(child, onerror=_unlock) if child.is_dir() else child.unlink()
            copy_scenario(saved.root, self.root)
        else:
            build()
            if saved is None and clean:
                _snapshots[key] = saved = _Snapshot(self.root)
        self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self._pristine = self.fingerprint()

    # --- helpers ---
    def save(self):
        self.state_path.write_text(json.dumps(self.state), encoding="utf-8")

    def load(self):
        self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
        return self.state["repos"][SLUG]

    def issue(self, n="7"):
        return self.state["repos"][SLUG]["issues"][n]

    def cli(self, *args, cwd=None, env=None):
        return run_py(VAULT_PY, args, config=self.cfg, cwd=cwd or self.clone, env=env)

    def ok(self, r):
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def refused(self, r, *words):
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        for w in words:
            self.assertIn(w.lower(), (r.stderr + r.stdout).lower())
        return r

    def calls(self, *prefix):
        self.load()
        return [c for c in self.state["calls"] if c[:len(prefix)] == list(prefix)]

    def note(self, name):
        return self.vault / "Agent" / "Work" / "widget" / f"widget-7-{name}.md"

    def fm(self, path):
        text = path.read_text(encoding="utf-8")
        return dict(ln.split(": ", 1) for ln in text.split("---")[1].strip().splitlines())

    def daily(self):
        p = self.vault / "Agent" / "Daily" / f"{dt.date.today().isoformat()}.md"
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def assert_valid(self):
        r = self.cli("validate")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn(": error:", r.stdout)

    def idea(self, *args):
        r = self.ok(self.cli("idea", "add", "Frobnicate the gadget", *args))
        return r.stdout.split()[-1].split("/")[-1]

    def body_file(self, text=ISSUE_BODY, name="body.md", raw=None):
        path = self.root / name
        if raw is not None:
            path.write_bytes(raw)
        else:
            path.write_text(text, encoding="utf-8", newline="\n")
        return str(path)

    def new_plan(self, text=SEALED_PLAN):
        self.ok(self.cli("new", "plan", "widget", "7"))
        self.note("plan").write_text(text, encoding="utf-8", newline="\n")

    def sealed(self, plan=SEALED_PLAN):
        self.new_plan(plan)
        self.ok(self.cli("seal", "plan", "widget", "7"))
        self.load()

    def branch(self, *extra):
        return self.cli("branch", "widget", "7", *extra)


class SealPlanTest(GhCase):  # T1 -> AC1, AC11
    def test_refuses_missing_plan(self):
        self.refused(self.cli("seal", "plan", "widget", "7"), "doesn't exist")

    def test_refuses_incomplete_plan(self):
        self.ok(self.cli("new", "plan", "widget", "7"))          # the bare scaffold
        self.refused(self.cli("seal", "plan", "widget", "7"), "can't be sealed")
        self.assertEqual(self.fm(self.note("plan"))["status"], "draft")
        self.assertEqual(self.calls("issue", "edit"), [])
        self.assertEqual(self.load()["labels"], ["bug"])

    def test_seal_records_everything(self):
        self.new_plan()
        self.ok(self.cli("seal", "plan", "widget", "7"))
        repo = self.load()
        fm = self.fm(self.note("plan"))
        self.assertEqual(fm["status"], "sealed")
        self.assertEqual(fm["issue_updated"], CREATED)
        self.assertEqual(fm["base_sha"], repo["head_sha"][:7])
        self.assertIn("planned", repo["labels"])                  # created because it was missing
        self.assertIn("planned", repo["issues"]["7"]["labels"])
        order = [c[:2] for c in self.state["calls"]]
        self.assertLess(order.index(["api", "graphql"]), order.index(["issue", "edit"]))
        self.assertEqual(order.count(["api", "graphql"]), 1)
        self.assertRegex(self.daily(), r"- \d\d:\d\d widget#7 planned\n")
        self.assert_valid()

    def test_a_feature_is_never_planned(self):     # T8 -> AC7
        self.issue()["labels"] = ["feature"]
        self.save()
        self.new_plan()
        self.refused(self.cli("seal", "plan", "widget", "7"), "feature")
        self.assertEqual(self.calls("label", "create"), [])
        self.assertEqual(self.calls("issue", "edit"), [])
        self.assertIn("status: draft", self.note("plan").read_text(encoding="utf-8"))
        self.sealed_feature_refused()

    def sealed_feature_refused(self):
        self.note("plan").write_text(self.note("plan").read_text(encoding="utf-8").replace(
            "status: draft", "status: sealed"), encoding="utf-8", newline="\n")
        self.refused(self.cli("seal", "plan", "widget", "7"), "feature")
        self.assertEqual(self.calls("issue", "edit"), [])

    def test_edit_time_is_the_latest_title_or_body_edit(self):
        self.issue()["body_edited"] = "2026-09-21T09:00:00Z"
        self.issue()["renamed"] = "2026-09-22T09:00:00Z"
        self.save()
        self.new_plan()
        self.ok(self.cli("seal", "plan", "widget", "7"))
        self.assertEqual(self.fm(self.note("plan"))["issue_updated"], "2026-09-22T09:00:00Z")

    def test_refuses_sealed_plan(self):
        self.sealed()
        before = self.note("plan").read_bytes()
        self.refused(self.cli("seal", "plan", "widget", "7"), "already sealed")
        self.assertEqual(before, self.note("plan").read_bytes())

    def test_sealed_plan_without_label_gets_it_back(self):
        self.sealed()
        self.issue()["labels"] = []
        self.save()
        before = self.note("plan").read_bytes()
        self.ok(self.cli("seal", "plan", "widget", "7"))
        self.assertIn("planned", self.load()["issues"]["7"]["labels"])
        self.assertEqual(before, self.note("plan").read_bytes())


class NoPrPlanTest(GhCase):  # T2, T3 -> AC2, AC3
    def test_seal_refuses_a_file_path(self):
        self.new_plan(SEALED_PLAN.replace("status: draft", "pr: none\nstatus: draft"))
        self.refused(self.cli("seal", "plan", "widget", "7"), "pr: none", "src/a.py", "src/b.py")
        self.assertIn("status: draft", self.note("plan").read_text(encoding="utf-8"))
        self.assertEqual(self.calls("issue", "edit"), [])

    def test_seal_accepts_files_none(self):
        self.sealed(NO_PR_PLAN)
        self.assertEqual(self.fm(self.note("plan"))["status"], "sealed")
        self.assertEqual(self.fm(self.note("plan"))["pr"], "none")
        self.assert_valid()

    def test_a_normal_plan_still_names_files(self):
        self.sealed()
        self.assertNotIn("pr", self.fm(self.note("plan")))

    def test_branch_and_open_pr_refuse(self):
        self.sealed(NO_PR_PLAN)
        self.refused(self.branch(), "pr: none")
        self.refused(self.cli("open-pr", "widget", "7"), "pr: none")
        self.assertEqual(git("branch", "--show-current", cwd=self.clone), "main")
        self.assertFalse(self.note("impl").exists() and "branch:" in self.note("impl").read_text(encoding="utf-8"))

    def test_preflight_and_stage_still_work(self):
        self.sealed(NO_PR_PLAN)
        self.ok(self.cli("preflight", "widget", "7"))
        self.assertTrue(self.ok(self.cli("stage", "widget", "7")).stdout.startswith("planned:"))


class PreflightTest(GhCase):  # T2 -> AC2
    def pre(self):
        return self.cli("preflight", "widget", "7")

    def test_missing_and_unsealed(self):
        self.refused(self.pre(), "doesn't exist")
        self.new_plan()
        self.refused(self.pre(), "not sealed")

    def test_closed_issue(self):
        self.sealed()
        self.issue()["state"] = "CLOSED"
        self.save()
        self.refused(self.pre(), "closed")

    def test_body_or_title_edit_fails(self):
        self.sealed()
        for field in ("body_edited", "renamed"):
            with self.subTest(field=field):
                self.issue()[field] = "2026-09-25T00:00:00Z"
                self.save()
                self.refused(self.pre(), "edited", "re-plan")
                self.issue()[field] = None
                self.save()

    def test_comments_and_labels_do_not_count(self):
        self.sealed()
        self.issue()["labels"] += ["needs-triage"]
        self.issue()["comment_at"] = "2026-09-25T00:00:00Z"
        self.save()
        self.ok(self.pre())
        query = " ".join(c[3] for c in self.calls("api", "graphql"))
        self.assertNotIn("updatedAt", query)

    def test_base_drift_warning_lists_overlap(self):
        self.sealed()
        self.state["repos"][SLUG]["compare"] = {"total_commits": 3, "files": [
            {"filename": "src/a.py"}, {"filename": "docs/x.md"}]}
        self.save()
        out = self.ok(self.pre()).stdout
        self.assertIn("warning: 3 commit(s)", out)
        self.assertIn("src/a.py", out)
        self.assertNotIn("docs/x.md", out)
        self.assertIn("preflight OK", out)

    def test_no_warning_without_overlap(self):
        self.sealed()
        self.state["repos"][SLUG]["compare"] = {"total_commits": 2, "files": [{"filename": "docs/x.md"}]}
        self.save()
        self.assertNotIn("warning", self.ok(self.pre()).stdout)


class BranchTest(GhCase):  # T3 -> AC3, AC11
    def test_creates_pushes_and_records(self):
        self.sealed()
        self.ok(self.branch())
        name = "feat/7-add-widget-frobbing"                       # the `feat:` prefix is dropped
        self.assertEqual(git("branch", "--show-current", cwd=self.clone), name)
        self.assertIn(name, git("ls-remote", "--heads", "origin", cwd=self.clone))
        self.assertEqual(git("rev-parse", "--abbrev-ref", "@{u}", cwd=self.clone), f"origin/{name}")
        self.assertEqual(self.fm(self.note("impl"))["branch"], name)
        self.assertRegex(self.daily(), rf"- \d\d:\d\d widget#7 started — {name}\n")
        self.assert_valid()

    def test_type_and_slug_options(self):
        self.sealed()
        self.ok(self.branch("--type", "chore", "--slug", "frob-fix"))
        self.assertEqual(git("branch", "--show-current", cwd=self.clone), "chore/7-frob-fix")

    def no_branch_made(self):
        self.assertEqual(git("branch", "--show-current", cwd=self.clone), "main")
        self.assertNotIn("/7-", git("branch", "-a", cwd=self.clone))
        self.assertNotIn("/7-", git("ls-remote", "--heads", "origin", cwd=self.clone))
        self.assertNotIn("branch", self.fm(self.note("impl")))

    def test_bug_issue_branches_as_fix(self):  # T1
        self.sealed()
        self.issue()["labels"].append("bug")
        self.save()
        for t in ("feat", "chore"):
            self.refused(self.branch("--type", t), "bug", "fix/")
            self.no_branch_made()
        r = self.branch("--type", "hotfix")
        self.assertEqual(r.returncode, 2)
        self.assertIn("invalid choice: 'hotfix'", r.stderr)
        self.no_branch_made()
        self.ok(self.branch())
        self.assertEqual(git("branch", "--show-current", cwd=self.clone), "fix/7-add-widget-frobbing")
        self.assertEqual(self.fm(self.note("impl"))["branch"], "fix/7-add-widget-frobbing")

    def test_bug_issue_accepts_explicit_fix(self):  # T1
        self.sealed()
        self.issue()["labels"].append("bug")
        self.save()
        self.ok(self.branch("--type", "fix", "--slug", "x"))
        self.assertEqual(git("branch", "--show-current", cwd=self.clone), "fix/7-x")

    def test_non_bug_refuses_fix_and_hotfix(self):  # T2
        self.sealed()
        self.refused(self.branch("--type", "fix"), "bug", "label")
        self.no_branch_made()
        r = self.branch("--type", "hotfix")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("hotfix", r.stderr)
        self.no_branch_made()

    def test_help_and_docstring(self):  # T3
        h = self.cli("branch", "--help").stdout
        self.assertNotIn("hotfix", h)
        self.assertIn("bug", h)
        line = next(ln for ln in VAULT_PY.read_text(encoding="utf-8").splitlines() if "branch <repo>" in ln)
        self.assertNotIn("--type feat", line)
        self.assertIn("--type", line)
        self.assertIn("bug", line)

    def test_refuses_existing_local_branch(self):
        self.sealed()
        git("branch", "chore/7-old", cwd=self.clone)
        self.refused(self.branch(), "already has a branch", "chore/7-old")
        self.assertEqual(git("branch", "--show-current", cwd=self.clone), "main")

    def test_refuses_existing_remote_branch(self):
        self.sealed()
        self.state["repos"][SLUG]["branches"] = ["fix/7-elsewhere"]
        self.save()
        self.refused(self.branch(), "already has a branch", "on GitHub")

    def test_refuses_bad_slug(self):
        self.sealed()
        self.refused(self.branch("--slug", "Bad Slug"), "must match")

    def test_needs_preflight_and_the_right_clone(self):
        self.new_plan()
        self.refused(self.branch(), "not sealed")
        git("remote", "set-url", "origin", "https://github.com/acme/other.git", cwd=self.clone)
        self.ok(self.cli("seal", "plan", "widget", "7"))
        self.refused(self.branch(), "acme/other", SLUG)


class OpenPrTest(GhCase):  # T4 -> AC4, AC11
    def started(self):
        self.sealed()
        self.ok(self.branch())
        self.name = "feat/7-add-widget-frobbing"

    def commit(self):
        (self.clone / "src.txt").write_text("x\n", encoding="utf-8")
        git("add", ".", cwd=self.clone)
        git("commit", "-q", "-m", "feat: x", cwd=self.clone)

    def test_refuses_wrong_branch_and_dirty_tree(self):
        self.started()
        git("switch", "-q", "-c", "chore/9-other", cwd=self.clone)
        self.refused(self.cli("open-pr", "widget", "7"), "current branch", self.name)
        git("switch", "-q", self.name, cwd=self.clone)
        (self.clone / "dirty.txt").write_text("x", encoding="utf-8")
        self.refused(self.cli("open-pr", "widget", "7"), "isn't clean")
        self.assertEqual(self.load()["created_prs"], [])

    def test_needs_a_recorded_branch(self):
        self.new_plan()
        self.refused(self.cli("open-pr", "widget", "7"), "no 'branch:'")

    def test_creates_pr_and_records_it(self):
        self.started()
        self.commit()
        body = self.root / "body.md"
        body.write_text("Summary here.\n", encoding="utf-8")
        self.ok(self.cli("open-pr", "widget", "7", "--body-file", str(body)))
        pr = self.load()["created_prs"][0]
        self.assertEqual(pr["title"], TITLE)
        self.assertEqual(pr["headRefName"], self.name)
        self.assertEqual(pr["base"], "main")
        self.assertTrue(pr["body"].startswith("Closes #7\n"))
        self.assertIn("Summary here.", pr["body"])
        self.assertEqual(self.fm(self.note("impl"))["pr"], "20")
        self.assertRegex(self.daily(), r"widget#7 pr-opened — https://github.com/acme/widget/pull/20\n")
        self.assertIn(self.name, git("ls-remote", "--heads", "origin", cwd=self.clone))
        self.assert_valid()

    def test_adopts_existing_open_pr(self):
        self.started()
        self.commit()
        self.state["repos"][SLUG]["prs"] = [{"number": 5, "headRefName": self.name,
                                             "url": "https://github.com/acme/widget/pull/5"}]
        self.save()
        self.ok(self.cli("open-pr", "widget", "7"))
        self.assertEqual(self.load()["created_prs"], [])
        self.assertEqual(self.fm(self.note("impl"))["pr"], "5")
        self.ok(self.cli("open-pr", "widget", "7"))               # rerun: recorded once, logged once
        self.assertEqual(self.daily().count("pr-opened"), 1)


CLAUDE_MD = ("# widget\n\n## Purpose\nThe widget repo.\n\n"
            "## Commands\n<!-- covers: README.md -->\n- Build: `python -m unittest`\n")
FULL_CLAUDE_AUDIT = "- Purpose: confirmed — still true\n- Commands: confirmed — still true\n"
FULL_OVERVIEW_AUDIT = ("- Purpose: confirmed — still true\n- Current state: confirmed — still true\n"
                       "- Architecture: confirmed — still true\n- Standing decisions: confirmed — still true\n")
README_MD = "# widget\n\n## Overview\nThe widget package.\n\n## Usage\nRun it.\n"
FULL_README_AUDIT = "- Overview: confirmed — still true\n- Usage: confirmed — still true\n"


class SealRetroTest(GhCase):  # T5-T9 -> AC5, AC6, AC7, AC9
    def prep(self, plan=SEALED_PLAN):
        self.name = "feat/7-add-widget-frobbing"
        self.snapshot(("retro", plan), lambda: self.build_prep(plan))
        self.retro = self.note("retro")

    def build_prep(self, plan):
        self.sealed(plan)
        self.ok(self.branch())
        (self.clone / "CLAUDE.md").write_text(CLAUDE_MD, encoding="utf-8", newline="\n")
        (self.clone / "README.md").write_text(README_MD, encoding="utf-8", newline="\n")
        git("add", "CLAUDE.md", "README.md", cwd=self.clone)
        git("commit", "-q", "-m", "docs: claude.md and readme", cwd=self.clone)
        git("push", "-q", "origin", self.name, cwd=self.clone)
        self.ok(self.cli("open-pr", "widget", "7"))
        self.ok(self.cli("new", "retro", "widget", "7"))

    def drop_readme(self):
        (self.clone / "README.md").unlink()
        git("add", "README.md", cwd=self.clone)
        git("commit", "-q", "-m", "chore: drop readme", cwd=self.clone)
        git("push", "-q", "origin", self.name, cwd=self.clone)

    def flatten_readme(self):
        (self.clone / "README.md").write_text("hi\n", encoding="utf-8", newline="\n")
        git("add", "README.md", cwd=self.clone)
        git("commit", "-q", "-m", "chore: flatten readme", cwd=self.clone)
        git("push", "-q", "origin", self.name, cwd=self.clone)

    def fill_retro(self, claude_lines, readme_lines, overview_lines):
        text = self.retro.read_text(encoding="utf-8")
        text = text.replace("## CLAUDE.md audit\n\n", "## CLAUDE.md audit\n" + claude_lines + "\n")
        text = text.replace("## README audit\n\n", "## README audit\n" + readme_lines + "\n")
        text = text.replace("## Overview audit\n\n", "## Overview audit\n" + overview_lines + "\n")
        self.retro.write_text(text, encoding="utf-8", newline="\n")

    def test_seals_and_bumps_verified(self):
        self.prep()
        self.fill_retro(FULL_CLAUDE_AUDIT, FULL_README_AUDIT, FULL_OVERVIEW_AUDIT)
        out = self.ok(self.cli("seal", "retro", "widget", "7")).stdout
        self.assertIn("Commands", out)
        claude_text = (self.clone / "CLAUDE.md").read_text(encoding="utf-8")
        today = dt.date.today().isoformat()
        self.assertIn(f"<!-- covers: README.md; verified: {today} -->", claude_text)
        self.assertIn("## Purpose\nThe widget repo.\n", claude_text)          # no covers: not bumped (D5)
        self.assertEqual(self.fm(self.note("impl"))["status"], "sealed")
        self.assertEqual(self.fm(self.retro)["status"], "sealed")
        proj = self.vault / "Agent" / "Projects" / "widgets.md"
        self.assertIn(f"audited: {today}", proj.read_text(encoding="utf-8"))
        self.assertRegex(self.daily(), r"widget#7 retro-done\n")
        log = git("log", "-1", "--format=%s", "origin/" + self.name, cwd=self.clone)
        self.assertIn("docs(claude): verify audited sections (#7)", log)
        self.assert_valid()

    def test_never_closes_the_issue_itself(self):         # T7 -> AC7
        self.prep()
        self.fill_retro(FULL_CLAUDE_AUDIT, FULL_README_AUDIT, FULL_OVERVIEW_AUDIT)
        self.ok(self.cli("seal", "retro", "widget", "7"))
        self.assertEqual(self.calls("issue", "close"), [])

    def test_a_no_pr_retro_on_a_pr_plan_is_refused(self):      # T6 -> AC6
        self.prep()
        self.fill_retro(FULL_CLAUDE_AUDIT, FULL_README_AUDIT, FULL_OVERVIEW_AUDIT)
        text = self.retro.read_text(encoding="utf-8").replace("pr: 20", "pr: none")
        self.retro.write_text(text + "\n## Closing comment\nbye\n", encoding="utf-8", newline="\n")
        self.refused(self.cli("seal", "retro", "widget", "7"), "must agree")
        self.assertEqual(self.calls("issue", "close"), [])

    def test_refuses_missing_audit_lines(self):
        self.prep()
        self.fill_retro("- Purpose: confirmed — still true\n", FULL_README_AUDIT, FULL_OVERVIEW_AUDIT)
        self.refused(self.cli("seal", "retro", "widget", "7"), "missing an audit line", "Commands")

    def test_refuses_duplicate_audit_lines(self):
        self.prep()
        dup = FULL_CLAUDE_AUDIT + "- Commands: confirmed — again\n"
        self.fill_retro(dup, FULL_README_AUDIT, FULL_OVERVIEW_AUDIT)
        self.refused(self.cli("seal", "retro", "widget", "7"), "more than one audit line")

    def test_refuses_removed_section_still_present(self):
        self.prep()
        removed = "- Purpose: confirmed — still true\n- Commands: removed — dropped\n"
        self.fill_retro(removed, FULL_README_AUDIT, FULL_OVERVIEW_AUDIT)
        self.refused(self.cli("seal", "retro", "widget", "7"), "marked removed but still there")

    def test_refuses_missing_readme_audit_lines(self):
        self.prep()
        self.fill_retro(FULL_CLAUDE_AUDIT, "- Overview: confirmed — still true\n", FULL_OVERVIEW_AUDIT)
        self.refused(self.cli("seal", "retro", "widget", "7"), "missing an audit line", "Usage")

    def test_refuses_duplicate_readme_audit_lines(self):
        self.prep()
        dup = FULL_README_AUDIT + "- Usage: confirmed — again\n"
        self.fill_retro(FULL_CLAUDE_AUDIT, dup, FULL_OVERVIEW_AUDIT)
        self.refused(self.cli("seal", "retro", "widget", "7"), "more than one audit line")

    def test_refuses_readme_removed_section_still_present(self):
        self.prep()
        removed = "- Overview: confirmed — still true\n- Usage: removed — dropped\n"
        self.fill_retro(FULL_CLAUDE_AUDIT, removed, FULL_OVERVIEW_AUDIT)
        self.refused(self.cli("seal", "retro", "widget", "7"), "marked removed but still there")

    def test_refuses_readme_audit_lines_when_no_readme(self):
        self.prep()
        self.drop_readme()
        self.fill_retro(FULL_CLAUDE_AUDIT, FULL_README_AUDIT, FULL_OVERVIEW_AUDIT)
        self.refused(self.cli("seal", "retro", "widget", "7"), "README audit", "None")

    def test_seals_with_none_readme_audit_when_no_readme(self):
        self.prep()
        self.drop_readme()
        self.fill_retro(FULL_CLAUDE_AUDIT, "None", FULL_OVERVIEW_AUDIT)
        self.ok(self.cli("seal", "retro", "widget", "7"))

    def test_refuses_none_readme_audit_when_readme_has_headings(self):
        self.prep()
        self.fill_retro(FULL_CLAUDE_AUDIT, "None", FULL_OVERVIEW_AUDIT)
        self.refused(self.cli("seal", "retro", "widget", "7"), "missing an audit line", "Overview", "Usage")

    def test_refuses_readme_audit_lines_when_readme_has_no_headings(self):
        self.prep()
        self.flatten_readme()
        self.fill_retro(FULL_CLAUDE_AUDIT, FULL_README_AUDIT, FULL_OVERVIEW_AUDIT)
        self.refused(self.cli("seal", "retro", "widget", "7"), "README audit", "None")

    def test_seals_with_none_readme_audit_when_readme_has_no_headings(self):
        self.prep()
        self.flatten_readme()
        self.fill_retro(FULL_CLAUDE_AUDIT, "None", FULL_OVERVIEW_AUDIT)
        self.ok(self.cli("seal", "retro", "widget", "7"))

    # --- lasting decisions (#36) ---
    def lasting_plan(self, *lines):
        return SEALED_PLAN.replace("## Decisions\n\n", "## Decisions\n" + "\n".join(lines) + "\n\n")

    def record(self, title, source, *extra):
        self.ok(self.cli("decision", "add", "widgets", title, "--decision", "d", "--why", "w",
                         "--source", source, *extra))

    def lasting_prep(self, *lines):
        self.prep(self.lasting_plan(*lines))
        self.fill_retro(FULL_CLAUDE_AUDIT, FULL_README_AUDIT, FULL_OVERVIEW_AUDIT)

    def test_refuses_a_lasting_decision_without_a_record(self):           # T7 -> AC7
        self.lasting_prep("- D1: a, because b", "- D2: keep it, because c (lasting)")
        head = git("rev-parse", "HEAD", cwd=self.clone)
        r = self.refused(self.cli("seal", "retro", "widget", "7"), "lasting decisions", "D2")
        self.assertNotIn("D1 ", r.stderr)
        self.assert_nothing_sealed(head)

    def test_seals_once_the_lasting_decision_has_its_record(self):        # T7 -> AC7
        self.lasting_prep("- D1: a, because b", "- D2: keep it, because c (user decision) (lasting)")
        self.record("Keep it", "plan widget#7 D2")
        self.ok(self.cli("seal", "retro", "widget", "7"))
        self.assertEqual(self.fm(self.retro)["status"], "sealed")
        self.assert_valid()

    def test_a_record_from_another_plan_decision_does_not_count(self):    # T7 -> AC7
        self.lasting_prep("- D1: keep it, because c (lasting)")
        self.record("Keep it", "plan widget#7 D2")
        self.refused(self.cli("seal", "retro", "widget", "7"), "D1")

    def test_a_replacing_decision_needs_its_record_to_replace(self):      # T7 -> AC7
        self.record("Old rule", "session")
        line = "- D1: reverse it, replaces [[widgets-d1-old-rule]], because c (lasting)"
        self.lasting_prep(line)
        self.record("New rule", "plan widget#7 D1")                       # recorded without --replaces
        head = git("rev-parse", "HEAD", cwd=self.clone)
        self.refused(self.cli("seal", "retro", "widget", "7"), "D1", "widgets-d1-old-rule")
        self.assert_nothing_sealed(head)

    def test_seals_when_the_replacing_record_replaces(self):              # T7 -> AC7
        self.record("Old rule", "session")
        self.lasting_prep("- D1: reverse it, replaces [[widgets-d1-old-rule]], because c (lasting)")
        self.record("New rule", "plan widget#7 D1", "--replaces", "widgets-d1-old-rule")
        self.ok(self.cli("seal", "retro", "widget", "7"))
        self.assert_valid()

    def test_plans_without_the_marker_need_nothing(self):                 # T7 -> AC7, AC12
        self.lasting_prep("- D1: a, because b", "- D2: c, because d (user decision)")
        self.ok(self.cli("seal", "retro", "widget", "7"))

    def add_findings(self, *lines):
        p = self.note("impl")
        text = p.read_text(encoding="utf-8").replace(
            "## Review findings\nNone\n", "## Review findings\n" + "\n".join(lines) + "\n")
        p.write_text(text, encoding="utf-8", newline="\n")

    def set_follow_ups(self, *lines):
        text = self.retro.read_text(encoding="utf-8").replace(
            "## Follow-ups\nNone\n", "## Follow-ups\n" + "\n".join(lines) + "\n")
        self.retro.write_text(text, encoding="utf-8", newline="\n")

    def unsorted_prep(self, *follow_ups):
        self.prep()
        self.fill_retro(FULL_CLAUDE_AUDIT, FULL_README_AUDIT, FULL_OVERVIEW_AUDIT)
        self.add_findings("- R1 (minor): Review 1: odd name → left open",
                          "- R2 (major): Review 1: bug → fixed in abc1234")
        if follow_ups:
            self.set_follow_ups(*follow_ups)

    def assert_nothing_sealed(self, head):
        self.assertEqual(self.fm(self.note("impl"))["status"], "open")
        self.assertEqual(self.fm(self.retro)["status"], "open")
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.clone), head)

    def test_refuses_unsorted_left_open_finding(self):    # T2 -> AC2
        self.unsorted_prep()
        head = git("rev-parse", "HEAD", cwd=self.clone)
        self.refused(self.cli("seal", "retro", "widget", "7"), "unsorted", "R1")
        self.assert_nothing_sealed(head)

    def test_refuses_unsorted_left_open_finding_on_closed_issue(self):    # T2 -> AC2
        self.unsorted_prep()
        self.issue()["state"] = "CLOSED"
        self.save()
        git("switch", "-q", "-c", "feat/9-other", cwd=self.clone)
        head = git("rev-parse", "HEAD", cwd=self.clone)
        self.refused(self.cli("seal", "retro", "widget", "7"), "unsorted", "R1")
        self.assert_nothing_sealed(head)

    def test_seals_when_left_open_finding_is_sorted(self):    # T3 -> AC4
        self.unsorted_prep("- dropped: cosmetic only (R1)")
        self.ok(self.cli("seal", "retro", "widget", "7"))
        self.assertEqual(self.fm(self.retro)["status"], "sealed")

    def test_seals_closed_issue_when_left_open_finding_is_sorted(self):    # T3 -> AC4
        self.unsorted_prep("- dropped: cosmetic only (R1, R2)")
        self.issue()["state"] = "CLOSED"
        self.save()
        git("switch", "-q", "-c", "feat/9-other", cwd=self.clone)
        self.ok(self.cli("seal", "retro", "widget", "7"))

    def test_refuses_finding_settled_twice(self):    # T4 -> AC3
        self.unsorted_prep("- dropped: cosmetic only (R1)", "- dropped: also cosmetic (R1)")
        self.refused(self.cli("seal", "retro", "widget", "7"), "more than one", "R1")

    def test_refuses_reference_to_missing_finding(self):    # T4 -> AC3
        self.unsorted_prep("- dropped: cosmetic only (R1, R9)")
        self.refused(self.cli("seal", "retro", "widget", "7"), "no review finding", "R9")

    def test_refuses_missing_notes(self):
        self.sealed()
        self.ok(self.branch())
        self.refused(self.cli("seal", "retro", "widget", "7"), "doesn't exist")

    def test_refuses_already_sealed(self):
        self.prep()
        self.fill_retro(FULL_CLAUDE_AUDIT, FULL_README_AUDIT, FULL_OVERVIEW_AUDIT)
        self.ok(self.cli("seal", "retro", "widget", "7"))
        self.refused(self.cli("seal", "retro", "widget", "7"), "already sealed")

    def test_refuses_invalid_as_sealed(self):
        self.prep()
        self.fill_retro(FULL_CLAUDE_AUDIT, FULL_README_AUDIT, FULL_OVERVIEW_AUDIT)
        text = self.retro.read_text(encoding="utf-8").replace(
            "## Follow-ups\nNone\n", "## Follow-ups\nnot a valid line\n")
        self.retro.write_text(text, encoding="utf-8", newline="\n")
        self.refused(self.cli("seal", "retro", "widget", "7"), "can't be sealed yet")

    def edit_pr(self, **changes):
        self.load()
        pr = self.state["repos"][SLUG]["prs"][0]
        pr.update(changes)
        self.save()
        return pr["number"]

    def filled_prep(self):
        self.prep()
        self.fill_retro(FULL_CLAUDE_AUDIT, FULL_README_AUDIT, FULL_OVERVIEW_AUDIT)
        return git("rev-parse", "HEAD", cwd=self.clone)

    def test_refuses_when_the_pr_no_longer_closes_the_issue(self):    # T2 -> AC1
        head = self.filled_prep()
        n = self.edit_pr(closes=[])
        self.refused(self.cli("seal", "retro", "widget", "7"), f"PR #{n}", "Closes #7")
        self.assert_nothing_sealed(head)
        self.assertEqual(git("rev-parse", f"origin/{self.name}", cwd=self.clone), head)

    def test_refuses_a_pr_that_is_no_longer_open(self):    # T3 -> AC2
        for state in ("MERGED", "CLOSED"):
            head = self.filled_prep() if state == "MERGED" else git("rev-parse", "HEAD", cwd=self.clone)
            n = self.edit_pr(closes=[], state=state)
            r = self.refused(self.cli("seal", "retro", "widget", "7"), f"PR #{n}", state, "by hand")
            self.assertNotIn("restore", (r.stderr + r.stdout).lower())
            self.assert_nothing_sealed(head)

    def test_closing_reference_must_match_number_and_repo(self):    # T4 -> AC1, AC4
        head = self.filled_prep()
        self.edit_pr(closes=[{"number": 7, "repo": "other/widget"}, {"number": 8, "repo": SLUG}])
        self.refused(self.cli("seal", "retro", "widget", "7"), "will not close issue #7")
        self.assert_nothing_sealed(head)
        self.edit_pr(closes=[{"number": 7, "repo": SLUG.upper()}])
        self.ok(self.cli("seal", "retro", "widget", "7"))
        self.assertEqual(self.fm(self.retro)["status"], "sealed")

    def test_closed_issue_makes_no_pr_query(self):    # T4 -> AC3
        self.filled_prep()
        self.edit_pr(closes=[], state="MERGED")
        self.issue()["state"] = "CLOSED"
        self.save()
        git("switch", "-q", "-c", "feat/9-other", cwd=self.clone)
        self.ok(self.cli("seal", "retro", "widget", "7"))
        self.assertEqual(self.calls("pr", "view"), [])

    def test_open_issue_whose_pr_closes_it_asks_github_once(self):    # T4 -> AC4, AC6
        self.filled_prep()
        self.ok(self.cli("seal", "retro", "widget", "7"))
        self.assertEqual(len(self.calls("pr", "view")), 1)

    # --- the merge (#51) ---
    def bare_rev(self, ref):
        return git("--git-dir", str(self.bare), "rev-parse", ref, cwd=self.root)

    def bump_subjects(self, ref):
        log = git("--git-dir", str(self.bare), "log", "--format=%s", f"main..{ref}", cwd=self.root)
        return [s for s in log.splitlines() if s == "docs(claude): verify audited sections (#7)"]

    def test_merges_the_pr_and_leaves_the_clone_on_main(self):    # T1 -> AC1, AC9
        self.filled_prep()
        checks = [{"__typename": "CheckRun", "name": c, "status": "COMPLETED", "conclusion": c.upper()}
                  for c in ("success", "neutral", "skipped")]
        checks.append({"__typename": "StatusContext", "context": "legacy", "state": "SUCCESS"})
        n = self.edit_pr(checks=checks)
        self.assertEqual(git("rev-parse", "--abbrev-ref", "main@{u}", cwd=self.clone, check=False), "")
        self.ok(self.cli("seal", "retro", "widget", "7"))
        sha = git("rev-parse", f"origin/{self.name}", cwd=self.clone)
        self.assertEqual(self.calls("pr", "merge"),
                         [["pr", "merge", str(n), "--repo", SLUG, "--squash", "--match-head-commit", sha]])
        self.assertEqual(self.fm(self.note("impl"))["status"], "sealed")
        self.assertEqual(self.fm(self.retro)["status"], "sealed")
        self.assertRegex(self.daily(), r"widget#7 retro-done\n")
        self.assertEqual(git("branch", "--show-current", cwd=self.clone), "main")
        self.assertEqual(git("rev-parse", "main", cwd=self.clone), self.bare_rev("main"))
        self.assertIn(f"verified: {dt.date.today().isoformat()}",
                      (self.clone / "CLAUDE.md").read_text(encoding="utf-8"))
        self.assertIn(self.name, git("branch", "--list", self.name, cwd=self.clone))
        self.assert_valid()

    def test_pulls_main_in_the_worktree_that_has_it(self):    # T1 -> AC1
        self.filled_prep()
        wt = self.root / "wt"
        git("worktree", "add", "-q", str(wt), "main", cwd=self.clone)
        self.ok(self.cli("seal", "retro", "widget", "7"))
        self.assertEqual(git("branch", "--show-current", cwd=self.clone), self.name)
        self.assertEqual(git("rev-parse", "main", cwd=wt), self.bare_rev("main"))
        self.assertEqual(self.fm(self.retro)["status"], "sealed")

    def test_a_failed_pull_in_that_worktree_keeps_the_seal_and_says_how_to_fix_it(self):    # T1 -> AC1
        self.filled_prep()
        wt = self.root / "wt"
        git("worktree", "add", "-q", str(wt), "main", cwd=self.clone)
        (wt / "README.md").write_text("mine\n", encoding="utf-8", newline="\n")   # a file the merge touches
        r = self.cli("seal", "retro", "widget", "7")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("merged", r.stderr)
        self.assertIn(str(wt), r.stderr)
        self.assertIn("pull --ff-only origin main", r.stderr)
        self.assertEqual(self.fm(self.retro)["status"], "sealed")
        self.assertRegex(self.daily(), r"widget#7 retro-done\n")

    def test_refuses_checks_that_have_not_passed(self):    # T2 -> AC2
        head = self.filled_prep()
        for check, word in (
                ({"__typename": "CheckRun", "name": "build", "status": "IN_PROGRESS", "conclusion": ""}, "build"),
                ({"__typename": "CheckRun", "name": "lint", "status": "COMPLETED", "conclusion": "FAILURE"}, "lint"),
                ({"__typename": "StatusContext", "context": "deploy", "state": "PENDING"}, "deploy")):
            self.edit_pr(checks=[check])
            self.refused(self.cli("seal", "retro", "widget", "7"), word)
            self.assert_nothing_sealed(head)
            self.assertEqual(self.bare_rev(self.name), head)
            self.assertEqual(self.calls("pr", "merge"), [])

    def test_refuses_a_pr_head_that_is_not_the_local_head(self):    # T2 -> AC2
        head = self.filled_prep()
        (self.clone / "extra.txt").write_text("x\n", encoding="utf-8")
        git("add", ".", cwd=self.clone)
        git("commit", "-q", "-m", "feat: unpushed", cwd=self.clone)
        local = git("rev-parse", "HEAD", cwd=self.clone)
        self.refused(self.cli("seal", "retro", "widget", "7"), head[:12], local[:12])
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.clone), local)
        self.assertEqual(self.bare_rev(self.name), head)
        self.assertEqual(self.calls("pr", "merge"), [])
        self.assertEqual(self.fm(self.retro)["status"], "open")

    def test_a_refused_merge_seals_nothing_and_the_rerun_adds_no_second_bump(self):    # T3 -> AC3
        self.filled_prep()
        self.load()
        self.state["repos"][SLUG]["refuse_merge"] = "Pull request is not mergeable: a required check is missing"
        self.save()
        self.refused(self.cli("seal", "retro", "widget", "7"), "required check is missing", "run this again")
        self.assertEqual(self.fm(self.note("impl"))["status"], "open")
        self.assertEqual(self.fm(self.retro)["status"], "open")
        self.assertNotIn("retro-done", self.daily())
        self.assertEqual(git("branch", "--show-current", cwd=self.clone), self.name)
        self.assertEqual(len(self.bump_subjects(self.name)), 1)
        self.load()
        self.state["repos"][SLUG]["refuse_merge"] = ""
        self.save()
        self.ok(self.cli("seal", "retro", "widget", "7"))
        self.assertEqual(len(self.bump_subjects(self.name)), 1)
        self.assertEqual(self.fm(self.retro)["status"], "sealed")
        self.assertEqual(git("branch", "--show-current", cwd=self.clone), "main")

    def test_a_branch_already_ending_in_the_bump_commit_gets_no_new_commit(self):    # T3 -> AC3
        self.filled_prep()
        git("commit", "-q", "--allow-empty", "-m", "docs(claude): verify audited sections (#7)", cwd=self.clone)
        git("push", "-q", cwd=self.clone)
        tip = git("rev-parse", "HEAD", cwd=self.clone)
        self.ok(self.cli("seal", "retro", "widget", "7"))
        self.assertEqual(self.bare_rev(self.name), tip)
        self.assertEqual(self.calls("pr", "merge")[0][-1], tip)

    def test_an_unpushed_bump_commit_is_pushed_as_is_and_merged(self):    # T3 -> AC3
        parent = self.filled_prep()
        git("commit", "-q", "--allow-empty", "-m", "docs(claude): verify audited sections (#7)", cwd=self.clone)
        tip = git("rev-parse", "HEAD", cwd=self.clone)
        self.assertEqual(self.bare_rev(self.name), parent)
        self.ok(self.cli("seal", "retro", "widget", "7"))
        self.assertEqual(self.bare_rev(self.name), tip)
        self.assertEqual(len(self.bump_subjects(self.name)), 1)
        self.assertEqual(self.calls("pr", "merge")[0][-1], tip)

    def test_a_closed_issue_never_merges(self):    # T4 -> AC4
        self.filled_prep()
        self.issue()["state"] = "CLOSED"
        self.save()
        git("switch", "-q", "-c", "feat/9-other", cwd=self.clone)
        self.ok(self.cli("seal", "retro", "widget", "7"))
        self.assertEqual(self.calls("pr", "merge"), [])
        self.assertEqual(git("branch", "--show-current", cwd=self.clone), "feat/9-other")

    def test_a_pr_that_is_already_merged_is_not_merged_again(self):    # T5 -> AC5
        self.filled_prep()
        self.edit_pr(state="MERGED")
        self.ok(self.cli("seal", "retro", "widget", "7"))
        self.assertEqual(self.calls("pr", "merge"), [])
        self.assertEqual(git("branch", "--show-current", cwd=self.clone), self.name)
        self.assertEqual(self.fm(self.retro)["status"], "sealed")

    def test_a_pr_that_is_closed_is_not_merged(self):    # T5 -> AC5
        self.filled_prep()
        self.edit_pr(state="CLOSED")
        self.ok(self.cli("seal", "retro", "widget", "7"))
        self.assertEqual(self.calls("pr", "merge"), [])
        self.assertEqual(self.fm(self.retro)["status"], "sealed")

    def test_refuses_wrong_branch_and_dirty_tree(self):
        self.prep()
        self.fill_retro(FULL_CLAUDE_AUDIT, FULL_README_AUDIT, FULL_OVERVIEW_AUDIT)
        git("switch", "-q", "-c", "chore/9-other", cwd=self.clone)
        self.refused(self.cli("seal", "retro", "widget", "7"), "current branch", self.name)
        git("switch", "-q", self.name, cwd=self.clone)
        (self.clone / "dirty.txt").write_text("x", encoding="utf-8")
        self.refused(self.cli("seal", "retro", "widget", "7"), "isn't clean")

    def drop_impl_branch_field(self):
        p = self.note("impl")
        p.write_text(p.read_text(encoding="utf-8").replace(f"branch: {self.name}\n", ""),
                     encoding="utf-8", newline="\n")

    def test_seals_closed_issue_from_other_branch_with_no_upstream(self):
        self.prep()
        self.fill_retro(FULL_CLAUDE_AUDIT, FULL_README_AUDIT, FULL_OVERVIEW_AUDIT)
        self.issue()["state"] = "CLOSED"
        self.save()
        self.drop_impl_branch_field()
        git("switch", "-q", "-c", "feat/9-other", cwd=self.clone)
        self.assertEqual(git("rev-parse", "--abbrev-ref", "feat/9-other@{u}", cwd=self.clone, check=False), "")
        main_before = git("rev-parse", "origin/main", cwd=self.clone)
        self.ok(self.cli("seal", "retro", "widget", "7"))
        self.assertEqual(git("rev-parse", "--abbrev-ref", "feat/9-other@{u}", cwd=self.clone),
                         "origin/feat/9-other")
        log = git("log", "-1", "--format=%s", "origin/feat/9-other", cwd=self.clone)
        self.assertIn("docs(claude): verify audited sections (#7)", log)
        self.assertEqual(git("rev-parse", "origin/main", cwd=self.clone), main_before)
        self.assertEqual(self.fm(self.note("impl"))["status"], "sealed")
        self.assertEqual(self.fm(self.retro)["status"], "sealed")

    def test_seals_closed_issue_ignoring_a_stale_branch_field(self):
        self.prep()
        self.fill_retro(FULL_CLAUDE_AUDIT, FULL_README_AUDIT, FULL_OVERVIEW_AUDIT)
        self.issue()["state"] = "CLOSED"
        self.save()
        git("switch", "-q", "-c", "feat/9-other", cwd=self.clone)
        self.assertEqual(self.fm(self.note("impl"))["branch"], self.name)   # left stale, unlike the other tests
        self.ok(self.cli("seal", "retro", "widget", "7"))
        self.assertEqual(self.fm(self.note("impl"))["status"], "sealed")

    def test_seals_closed_issue_from_branch_with_existing_upstream(self):
        self.prep()
        self.fill_retro(FULL_CLAUDE_AUDIT, FULL_README_AUDIT, FULL_OVERVIEW_AUDIT)
        self.issue()["state"] = "CLOSED"
        self.save()
        self.assertEqual(git("rev-parse", "--abbrev-ref", f"{self.name}@{{u}}", cwd=self.clone),
                         f"origin/{self.name}")
        self.ok(self.cli("seal", "retro", "widget", "7"))
        log = git("log", "-1", "--format=%s", f"origin/{self.name}", cwd=self.clone)
        self.assertIn("docs(claude): verify audited sections (#7)", log)

    def test_closed_issue_refused_on_default_branch(self):
        self.prep()
        self.fill_retro(FULL_CLAUDE_AUDIT, FULL_README_AUDIT, FULL_OVERVIEW_AUDIT)
        self.issue()["state"] = "CLOSED"
        self.save()
        self.drop_impl_branch_field()
        git("switch", "-q", "main", cwd=self.clone)
        self.refused(self.cli("seal", "retro", "widget", "7"), "closed", "main")

    def test_closed_issue_refused_when_detached(self):
        self.prep()
        self.fill_retro(FULL_CLAUDE_AUDIT, FULL_README_AUDIT, FULL_OVERVIEW_AUDIT)
        self.issue()["state"] = "CLOSED"
        self.save()
        self.drop_impl_branch_field()
        git("switch", "-q", "--detach", "HEAD", cwd=self.clone)
        self.refused(self.cli("seal", "retro", "widget", "7"), "closed", "detached")

    def test_closed_issue_refused_with_dirty_tree(self):
        self.prep()
        self.fill_retro(FULL_CLAUDE_AUDIT, FULL_README_AUDIT, FULL_OVERVIEW_AUDIT)
        self.issue()["state"] = "CLOSED"
        self.save()
        self.drop_impl_branch_field()
        git("switch", "-q", "-c", "feat/9-other", cwd=self.clone)
        (self.clone / "dirty.txt").write_text("x", encoding="utf-8")
        self.refused(self.cli("seal", "retro", "widget", "7"), "isn't clean")


class NoPrRetroTest(GhCase):  # T6, T7 -> AC6, AC7
    fill_retro = SealRetroTest.fill_retro
    add_findings = SealRetroTest.add_findings
    set_follow_ups = SealRetroTest.set_follow_ups
    lasting_plan = SealRetroTest.lasting_plan
    record = SealRetroTest.record
    COMMENT = "Done in the vault.\nSecond line."

    def prep(self, plan=NO_PR_PLAN, comment=COMMENT):
        self.snapshot(("no-pr-retro", plan, comment), lambda: self.build_prep(plan, comment))
        self.retro = self.note("retro")
        self.head = git("rev-parse", "HEAD", cwd=self.clone)

    def build_prep(self, plan, comment):
        self.sealed(plan)
        (self.clone / "CLAUDE.md").write_text(CLAUDE_MD, encoding="utf-8", newline="\n")
        (self.clone / "README.md").write_text(README_MD, encoding="utf-8", newline="\n")
        git("add", "CLAUDE.md", "README.md", cwd=self.clone)
        git("commit", "-q", "-m", "docs: claude.md and readme", cwd=self.clone)
        git("push", "-q", "origin", "main", cwd=self.clone)
        self.ok(self.cli("new", "retro", "widget", "7"))
        self.retro = self.note("retro")
        self.fill_retro(FULL_CLAUDE_AUDIT, FULL_README_AUDIT, FULL_OVERVIEW_AUDIT)
        text = self.retro.read_text(encoding="utf-8")
        text = text.replace("<!-- The comment the issue is closed with; the user approves it with this retro. -->\n",
                            "<!-- hint -->\n" + comment + "\n")
        self.retro.write_text(text, encoding="utf-8", newline="\n")
        self.head = git("rev-parse", "HEAD", cwd=self.clone)

    def sealed_nothing(self):
        self.assertEqual(self.fm(self.note("impl"))["status"], "open")
        self.assertEqual(self.fm(self.retro)["status"], "open")

    def test_closes_with_the_comment_and_seals_without_a_commit(self):
        self.prep()
        out = self.ok(self.cli("seal", "retro", "widget", "7")).stdout
        self.assertIn("closed issue #7", out)
        issue = self.load()["issues"]["7"]
        self.assertEqual(issue["state"], "CLOSED")
        self.assertEqual(issue["closed_comment"], self.COMMENT)       # verbatim, no HTML comment
        self.assertEqual(issue["closed_reason"], "completed")
        self.assertEqual(self.fm(self.note("impl"))["status"], "sealed")
        self.assertEqual(self.fm(self.retro)["status"], "sealed")
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.clone), self.head)
        self.assertIn(f"audited: {dt.date.today().isoformat()}",
                      (self.vault / "Agent" / "Projects" / "widgets.md").read_text(encoding="utf-8"))
        self.assertNotIn("verified:", (self.clone / "CLAUDE.md").read_text(encoding="utf-8"))
        self.assertEqual(self.calls("pr"), [])
        self.assert_valid()

    def test_works_from_any_branch_with_a_dirty_tree(self):
        self.prep()
        git("switch", "-q", "-c", "scratch", cwd=self.clone)
        (self.clone / "junk.txt").write_text("x", encoding="utf-8")
        self.ok(self.cli("seal", "retro", "widget", "7"))

    def test_an_already_closed_issue_is_only_sealed(self):
        self.prep()
        self.issue()["state"] = "CLOSED"
        self.save()
        self.ok(self.cli("seal", "retro", "widget", "7"))
        self.assertEqual(self.calls("issue", "close"), [])
        self.assertEqual(self.fm(self.retro)["status"], "sealed")

    def test_a_failed_close_seals_nothing(self):
        self.prep()
        self.state["refuse_close"] = "HTTP 500"
        self.save()
        self.refused(self.cli("seal", "retro", "widget", "7"), "nothing is sealed")
        self.sealed_nothing()
        self.assertEqual(self.load()["issues"]["7"]["state"], "OPEN")

    def test_retro_pr_must_agree_with_the_plan(self):
        self.prep()
        self.retro.write_text(self.retro.read_text(encoding="utf-8").replace("pr: none", "pr: 5"),
                              encoding="utf-8", newline="\n")
        self.refused(self.cli("seal", "retro", "widget", "7"), "must agree")
        self.sealed_nothing()
        self.assertEqual(self.calls("issue", "close"), [])

    def test_empty_closing_comment_is_refused(self):
        self.prep(comment="")
        self.refused(self.cli("seal", "retro", "widget", "7"), "Closing comment")
        self.sealed_nothing()
        self.assertEqual(self.calls("issue", "close"), [])

    def test_still_requires_the_audit(self):
        self.prep()
        text = self.retro.read_text(encoding="utf-8").replace("- Commands: confirmed — still true\n", "")
        self.retro.write_text(text, encoding="utf-8", newline="\n")
        self.refused(self.cli("seal", "retro", "widget", "7"), "missing an audit line")
        self.assertEqual(self.calls("issue", "close"), [])

    def test_still_requires_sorted_follow_ups(self):
        self.prep()
        self.add_findings("- R1 (minor): Review 1: odd name → left open")
        self.refused(self.cli("seal", "retro", "widget", "7"), "unsorted", "R1")
        self.assertEqual(self.calls("issue", "close"), [])

    def test_still_requires_recorded_lasting_decisions(self):
        self.prep(NO_PR_PLAN.replace("## Decisions\n\n", "## Decisions\n- D1: keep it, because c (lasting)\n\n"))
        self.refused(self.cli("seal", "retro", "widget", "7"), "lasting decisions", "D1")
        self.assertEqual(self.calls("issue", "close"), [])


class StageTest(GhCase):  # T5 -> AC5
    def stage(self):
        r = self.ok(self.cli("stage", "widget", "7"))
        self.assertEqual(len(r.stdout.strip().splitlines()), 1)
        return r.stdout.strip()

    def test_backlog_without_plan_or_with_draft(self):
        self.assertTrue(self.stage().startswith("backlog:"))
        self.new_plan()
        self.assertTrue(self.stage().startswith("backlog:"))

    def test_sealed_plan_without_label_says_to_rerun_seal(self):
        self.sealed()
        self.issue()["labels"] = []
        self.save()
        out = self.stage()
        self.assertTrue(out.startswith("backlog:"))
        self.assertIn("seal plan widget 7", out)

    def test_planned(self):
        self.sealed()
        self.assertTrue(self.stage().startswith("planned:"))

    def test_in_progress_and_in_review(self):
        self.sealed()
        self.state["repos"][SLUG]["branches"] = ["feat/7-x", "feat/70-other"]
        self.save()
        self.assertTrue(self.stage().startswith("in progress:"))
        self.state["repos"][SLUG]["prs"] = [{"number": 3, "headRefName": "feat/7-x", "url": "u/3"}]
        self.save()
        self.assertTrue(self.stage().startswith("in review:"))

    def test_other_issues_branches_do_not_count(self):
        self.sealed()
        self.state["repos"][SLUG]["branches"] = ["feat/70-other"]
        self.save()
        self.assertTrue(self.stage().startswith("planned:"))

    def test_a_feature_reports_its_sub_issues(self):     # T8, T9 -> AC7
        self.issue()["labels"] = ["feature"]
        self.issue()["sub_summary"] = {"total": 3, "completed": 1}
        self.state["repos"][SLUG]["branches"] = ["feat/7-x"]
        self.state["repos"][SLUG]["prs"] = [{"number": 3, "headRefName": "feat/7-x", "url": "u/3"}]
        self.save()
        self.assertEqual(self.stage(), "feature: 2 of 3 sub-issues open")
        self.issue()["state"] = "CLOSED"
        self.save()
        self.assertTrue(self.stage().startswith("done:"))

    def test_done(self):
        self.sealed()
        self.state["repos"][SLUG]["branches"] = ["feat/7-x"]
        self.issue()["state"] = "CLOSED"
        self.save()
        self.assertTrue(self.stage().startswith("done:"))


class PromoteTest(GhCase):  # T4 -> AC4 (T6 -> AC6, AC11 before #27)
    def promote(self, name, *extra, body=None):
        if not any(f in extra for f in ("--parent", "--standalone", "--bug")):
            extra += ("--standalone",)
        return self.cli("idea", "promote", name, "--body-file", body or self.body_file(), *extra)

    def test_promotes_into_the_projects_single_repo(self):
        name = self.idea("--project", "widgets")
        self.ok(self.promote(name))
        made = self.load()["created_issues"][0]
        self.assertEqual(made["title"], "Frobnicate the gadget")
        self.assertEqual(made["body"], ISSUE_BODY)          # no "Promoted from" line
        fm = self.fm(self.vault / "Agent" / "Ideas" / name)
        self.assertEqual(fm["status"], "promoted")
        self.assertEqual(fm["promoted_to"].strip('"'), made["url"])
        self.assertRegex(self.daily(), r"idea-promoted — Frobnicate the gadget \[\[")
        self.assert_valid()

    def test_body_file_is_required_and_checked(self):
        name = self.idea("--project", "widgets")
        r = self.cli("idea", "promote", name)
        self.assertEqual(r.returncode, 2, r.stderr)                       # argparse refuses
        untemplated = self.body_file("Just some text.\n", "plain.md")
        self.refused(self.promote(name, body=untemplated), "template", "missing section")
        self.assertEqual(self.load()["created_issues"], [])
        self.assertEqual(self.fm(self.vault / "Agent" / "Ideas" / name)["status"], "open")

    def test_title_override(self):
        name = self.idea("--project", "widgets")
        self.ok(self.promote(name, "--title", "feat: frobnicate"))
        self.assertEqual(self.load()["created_issues"][0]["title"], "feat: frobnicate")

    def test_refuses_dropped_and_promoted_ideas(self):
        name = self.idea("--project", "widgets")
        self.ok(self.cli("idea", "drop", name, "--reason", "no"))
        self.refused(self.promote(name), "dropped")
        other = self.idea("--project", "widgets")
        self.ok(self.promote(other))
        self.refused(self.promote(other), "promoted")
        self.assertEqual(len(self.load()["created_issues"]), 1)

    def test_repo_required_without_project_or_with_many_repos(self):
        self.refused(self.promote(self.idea()), "--repo")
        self.ok(self.cli("new", "project", "multi", "--repo", "acme/a", "--repo", "acme/b"))
        self.ok(self.cli("new", "project", "empty"))
        multi, empty = self.idea("--project", "multi"), self.idea("--project", "empty")
        self.refused(self.promote(multi), "--repo")
        self.refused(self.promote(empty), "0 repos")
        self.ok(self.promote(multi, "--repo", "b"))
        self.load()
        self.assertEqual(self.state["repos"]["acme/b"]["created_issues"][0]["title"], "Frobnicate the gadget")
        self.refused(self.promote(self.idea("--project", "multi"), "--repo", "widget"), "doesn't belong")
        self.refused(self.promote(self.idea(), "--repo", "nope"), "not listed")


class IssueCreateTest(GhCase):  # T2, T3 -> AC2, AC3
    def create(self, *extra, repo="widget", title="feat: frob", body=None):
        if not any(f in extra for f in ("--feature", "--parent", "--standalone", "--bug")):
            extra += ("--standalone",)
        return self.cli("issue", "create", repo, "--title", title, "--body-file", body or self.body_file(), *extra)

    def test_creates_the_issue_with_the_files_text(self):
        r = self.ok(self.create())
        made = self.load()["created_issues"]
        self.assertEqual(len(made), 1)
        self.assertEqual((made[0]["title"], made[0]["body"]), ("feat: frob", ISSUE_BODY))
        self.assertEqual(r.stdout.strip().splitlines()[-1], made[0]["url"])
        self.assertEqual(self.daily(), "")                    # plain issue create logs nothing

    def test_bom_and_crlf_are_normalised(self):
        raw = b"\xef\xbb\xbf" + ISSUE_BODY.replace("\n", "\r\n").encode("utf-8")
        self.ok(self.create(body=self.body_file(raw=raw)))
        self.assertEqual(self.load()["created_issues"][0]["body"], ISSUE_BODY)

    def test_each_mismatch_is_refused_without_calling_gh(self):
        cases = {
            "missing": (ISSUE_BODY.replace("## Constraints\nNone\n\n", ""), "missing section '## Constraints'"),
            "reordered": (ISSUE_BODY.replace("## Problem", "## X").replace("## Source", "## Problem")
                          .replace("## X", "## Source"), "out of order"),
            "duplicated": (ISSUE_BODY + "\n## Source\nagain\n", "appears 2 times"),
            "unknown": (ISSUE_BODY + "\n## Extra\nx\n", "unknown section"),
            "empty": (ISSUE_BODY.replace("session\n", "<!-- hint -->\n"), "'## Source' is empty"),
            "leading": ("Intro.\n\n" + ISSUE_BODY, "before the first"),
        }
        for label, (text, want) in cases.items():
            with self.subTest(label):
                self.refused(self.create(body=self.body_file(text)), want)
        self.assertEqual(self.calls("issue", "create"), [])

    def test_two_problems_are_both_named(self):
        text = "Intro.\n\n" + ISSUE_BODY.replace("## Constraints\nNone\n\n", "")
        self.refused(self.create(body=self.body_file(text)), "before the first", "missing section")

    def test_other_refusals(self):
        self.refused(self.create(body=self.body_file(raw=ISSUE_BODY.encode("utf-16"))), "UTF-8")
        self.refused(self.create(body=str(self.root / "nope.md")), "can't read")
        self.refused(self.create(title="  "), "title")
        self.refused(self.create(repo="nope"), "not listed")
        self.assertEqual(self.calls("issue", "create"), [])

    def test_idea_is_promoted(self):
        name = self.idea("--project", "widgets")
        r = self.ok(self.create("--idea", name))
        url = self.load()["created_issues"][0]["url"]
        self.assertEqual(r.stdout.strip().splitlines()[-1], url)
        fm = self.fm(self.vault / "Agent" / "Ideas" / name)
        self.assertEqual((fm["status"], fm["promoted_to"].strip('"')), ("promoted", url))
        self.assertRegex(self.daily(), r"idea-promoted — feat: frob \[\[")
        self.assert_valid()

    def test_idea_without_project_goes_to_the_named_repo(self):
        self.ok(self.create("--idea", self.idea()))
        self.assertEqual(len(self.load()["created_issues"]), 1)

    def test_idea_that_cannot_be_promoted_is_refused(self):
        dropped = self.idea("--project", "widgets")
        self.ok(self.cli("idea", "drop", dropped, "--reason", "no"))
        self.refused(self.create("--idea", dropped), "dropped")
        done = self.idea("--project", "widgets")
        self.ok(self.create("--idea", done))
        self.refused(self.create("--idea", done), "promoted")
        self.ok(self.cli("new", "project", "other", "--repo", "acme/other"))
        elsewhere = self.idea("--project", "other")
        self.refused(self.create("--idea", elsewhere), "doesn't belong")
        self.assertEqual(len(self.load()["created_issues"]), 1)


class RepoInitTest(GhCase):  # T7, T8 -> AC7, AC8
    def init(self):
        return self.cli("repo-init", str(self.clone), cwd=self.root)

    def test_fresh_clone_gets_everything(self):
        self.ok(self.init())
        repo = self.load()
        self.assertEqual(repo["settings"], {"allow_squash_merge": True, "allow_merge_commit": False,
                                            "allow_rebase_merge": False, "delete_branch_on_merge": True})
        self.assertEqual(repo["labels"], ["bug", "planned", "feature"])      # T10 -> AC8: bug was already there
        self.assertEqual(len(self.calls("label", "create")), 2)
        hooks = git("config", "--local", "core.hooksPath", cwd=self.clone)
        self.assertEqual(Path(hooks), CODE / "githooks")
        text = (self.clone / "CLAUDE.md").read_text(encoding="utf-8")
        for section in ("Purpose", "Commands", "Layout", "Conventions", "Gotchas"):
            self.assertIn(f"## {section}", text)
        self.assertIn("widgets", text)
        self.assertIn("- Lint: `<command>`", text)                                       # T2 -> AC2 (#33)
        status = git("status", "--porcelain", "-uall", cwd=self.clone).splitlines()      # not committed
        self.assertEqual(sorted(status), ["?? .github/ISSUE_TEMPLATE/issue.md", "?? CLAUDE.md"])
        issue = (self.clone / ".github" / "ISSUE_TEMPLATE" / "issue.md").read_text(encoding="utf-8")
        for section in ("Problem", "Desired outcome", "Constraints", "Out of scope", "Source"):
            self.assertIn(f"## {section}\n", issue)
        self.assertEqual(issue.encode("utf-8"), (REPO / lib.ISSUE_TEMPLATE_PATH).read_bytes())

    def test_a_repo_with_neither_label_gets_both(self):
        self.state["repos"][SLUG]["labels"] = []
        self.save()
        out = self.ok(self.init()).stdout
        self.assertEqual(self.load()["labels"], ["planned", "feature", "bug"])
        for label in ("planned", "feature", "bug"):
            self.assertIn(f"label '{label}': created", out)

    def test_existing_issue_template_is_left_alone(self):
        path = self.clone / ".github" / "ISSUE_TEMPLATE" / "issue.md"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"mine\n")
        r = self.ok(self.init())
        self.assertEqual(path.read_bytes(), b"mine\n")
        self.assertIn("issue.md: already exists, left alone", r.stdout)

    def test_checked_in_issue_template_matches_repo_init(self):     # T8 -> AC10
        checked_in = (REPO / lib.ISSUE_TEMPLATE_PATH).read_bytes()
        self.assertEqual(checked_in, lib.issue_template_file().encode("utf-8"))

    def test_rerun_changes_nothing(self):
        self.ok(self.init())
        tpl = self.clone / ".github" / "ISSUE_TEMPLATE" / "issue.md"
        before = (self.clone / "CLAUDE.md").read_bytes(), self.load(), git("config", "--local", "-l", cwd=self.clone), tpl.read_bytes()
        n_calls = len(self.state["calls"])
        self.ok(self.init())
        self.assertEqual(before, ((self.clone / "CLAUDE.md").read_bytes(), self.load(),
                                  git("config", "--local", "-l", cwd=self.clone), tpl.read_bytes()))
        writes = [c for c in self.state["calls"][n_calls:] if "PATCH" in c or c[:2] == ["label", "create"]]
        self.assertEqual(writes, [])

    def test_unregistered_repo_refused(self):
        git("remote", "set-url", "origin", "https://github.com/acme/unlisted.git", cwd=self.clone)
        self.refused(self.init(), "acme/unlisted", "not listed")
        self.assertEqual(self.calls("api"), [])
        self.assertFalse((self.clone / "CLAUDE.md").exists())

    def test_existing_claude_md_left_alone(self):
        (self.clone / "CLAUDE.md").write_text("mine\n", encoding="utf-8")
        self.ok(self.init())
        self.assertEqual((self.clone / "CLAUDE.md").read_text(encoding="utf-8"), "mine\n")

    def test_refused_settings_name_the_manual_steps(self):
        self.state["repos"][SLUG]["refuse_settings"] = True
        self.save()
        r = self.refused(self.init(), "Settings", "Pull Requests", "Squash")
        self.assertNotIn("planned", self.load()["labels"])
        self.assertEqual(r.returncode, 1)

    def test_foreign_hooks_path_refused(self):
        theirs = self.root / "theirs"
        theirs.mkdir()
        (theirs / "pre-push").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        git("config", "core.hooksPath", str(theirs), cwd=self.clone)
        self.refused(self.init(), "core.hooksPath", "isn't agent-vault's")
        self.assertEqual(git("config", "core.hooksPath", cwd=self.clone), str(theirs))
        self.assertEqual(self.calls("api"), [])                  # refused before any change

    def test_own_hook_at_another_path_is_accepted(self):
        git("config", "core.hooksPath", str(REPO / "agent_vault" / "githooks"), cwd=self.clone)
        self.ok(self.init())
        self.assertEqual(git("config", "core.hooksPath", cwd=self.clone),
                         str(REPO / "agent_vault" / "githooks"))

    def test_signed_out_gh_is_reported_not_a_settings_hint(self):
        self.state["signed_out"] = True
        self.save()
        r = self.refused(self.init(), "gh auth login")
        self.assertNotIn("Pull Requests", r.stderr)


class GhHelperTest(GhCase):  # T10 -> AC10
    def test_signed_out(self):
        self.state["signed_out"] = True
        self.save()
        self.refused(self.cli("stage", "widget", "7"), "not signed in", "gh auth login")

    def test_override_wins(self):
        self.assertEqual(github.gh_command(), [sys.executable, str(FAKE_GH)])

    def test_override_pointing_nowhere(self):
        with mock.patch.dict(os.environ, {"AGENT_VAULT_GH": str(self.root / "nope.py")}):
            with self.assertRaises(github.CmdError) as cm:
                github.gh_command()
        self.assertEqual(cm.exception.kind, "missing")
        self.assertIn("AGENT_VAULT_GH", str(cm.exception))

    def test_missing_gh(self):
        env = {k: v for k, v in os.environ.items() if k != "AGENT_VAULT_GH"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(github.shutil, "which", return_value=None), \
                mock.patch.object(github, "_windows_gh_paths", return_value=[self.root / "no" / "gh.exe"]):
            with self.assertRaises(github.CmdError) as cm:
                github.gh_command()
        self.assertEqual(cm.exception.kind, "missing")
        self.assertIn("cli.github.com", str(cm.exception))

    def test_falls_back_to_the_standard_windows_path(self):
        exe = self.root / "GitHub CLI" / "gh.exe"
        exe.parent.mkdir()
        exe.write_text("", encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k != "AGENT_VAULT_GH"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(github.shutil, "which", return_value=None), \
                mock.patch.object(github.sys, "platform", "win32"), \
                mock.patch.object(github, "_windows_gh_paths", return_value=[exe]):
            self.assertEqual(github.gh_command(), [str(exe)])

    def test_missing_gh_through_the_cli(self):
        r = self.cli("stage", "widget", "7", env={"AGENT_VAULT_GH": str(self.root / "nope.py")})
        self.refused(r, "AGENT_VAULT_GH")

    def test_only_gh_and_git_run(self):
        with self.assertRaises(ValueError):
            github.run(["rm", "-rf", "x"])

    def test_origin_slug_forms(self):
        for url, want in (("https://github.com/o/n.git", "o/n"), ("git@github.com:o/n.git", "o/n"),
                          ("https://github.com/o/n", "o/n")):
            git("config", "remote.origin.url", url, cwd=self.clone)
            self.assertEqual(github.origin_slug(self.clone), want)
        git("config", "remote.origin.url", "https://example.com/o/n.git", cwd=self.clone)
        with self.assertRaises(github.CmdError):
            github.origin_slug(self.clone)

    def add_pair(self):
        """Issue 30 (a feature) with issue 31 filed under it, in SLUG."""
        issues = self.state["repos"][SLUG]["issues"]
        issues["30"] = {"state": "OPEN", "title": "F", "created": CREATED, "body_edited": None,
                        "renamed": None, "labels": ["feature"]}
        issues["31"] = {"state": "OPEN", "title": "S", "created": CREATED, "body_edited": None,
                        "renamed": None, "labels": [], "parent": f"https://github.com/{SLUG}/issues/30"}
        self.state["repos"][SLUG]["labels"].append("feature")
        self.save()

    def test_issue_info_reads_the_parent_and_sub_issue_counts(self):    # T1 -> AC9
        self.add_pair()
        child = github.issue_info(SLUG, "31")
        self.assertEqual(child["parent"], {"number": 30, "url": f"https://github.com/{SLUG}/issues/30"})
        parent = github.issue_info(SLUG, "30")
        self.assertIsNone(parent["parent"])
        self.assertEqual((parent["sub_total"], parent["sub_completed"]), (1, 0))
        self.assertEqual(len(self.calls("api", "graphql")), 2)

    def test_repo_issues_return_labels_and_parent(self):
        self.add_pair()
        by_number = {i["number"]: i for i in github.repo_issues(SLUG)}
        self.assertEqual(by_number[30]["labels"], ["feature"])
        self.assertIsNone(by_number[30]["parent"])
        self.assertEqual(by_number[31]["parent"]["number"], 30)

    def test_open_features_lists_only_the_labelled_open_ones(self):
        self.add_pair()
        self.state["repos"][SLUG]["issues"]["32"] = {
            "state": "CLOSED", "title": "G", "created": CREATED, "body_edited": None, "renamed": None,
            "labels": ["feature"]}
        self.save()
        found = github.open_features(SLUG, "feature")
        self.assertEqual([(f["number"], f["total"], f["completed"]) for f in found], [(30, 1, 0)])
        self.assertIn(["issue", "list", "--repo", SLUG, "--state", "open", "--label", "feature", "--limit",
                       "5000", "--json", "number,title,url,body,subIssuesSummary"], self.calls("issue", "list"))

    def test_creating_with_a_parent_and_a_label_sends_both(self):
        self.add_pair()
        args = github.issue_create_args(SLUG, "T", parent=f"https://github.com/{SLUG}/issues/30", label="feature")
        self.assertEqual(args, ["issue", "create", "--repo", SLUG, "--title", "T", "--parent",
                                f"https://github.com/{SLUG}/issues/30", "--label", "feature"])
        self.assertNotIn("--parent", github.issue_create_args(SLUG, "T"))
        github.gh(*args, "--body-file", self.body_file())
        made = self.load()["created_issues"][-1]
        self.assertEqual((made["parent"], made["label"]), (f"https://github.com/{SLUG}/issues/30", "feature"))

    def test_add_sub_issue_sets_the_parent_and_reports_a_refusal(self):
        self.add_pair()
        issues = self.state["repos"][SLUG]["issues"]
        issues["33"] = {"state": "OPEN", "title": "H", "created": CREATED, "body_edited": None,
                        "renamed": None, "labels": []}
        self.state["refuse_sub_issue"] = {f"https://github.com/{SLUG}/issues/34": "already has a parent"}
        self.save()
        github.add_sub_issue(SLUG, "30", f"https://github.com/{SLUG}/issues/33")
        self.assertEqual(github.issue_info(SLUG, "30")["sub_total"], 2)
        with self.assertRaises(github.CmdError) as cm:
            github.add_sub_issue(SLUG, "30", f"https://github.com/{SLUG}/issues/34")
        self.assertIn("already has a parent", str(cm.exception))

    def test_an_old_gh_is_told_to_update(self):
        self.add_pair()
        self.state["old_gh"] = True
        self.save()
        url = f"https://github.com/{SLUG}/issues/30"
        for call in (lambda: github.gh(*github.issue_create_args(SLUG, "T", parent=url), "--body-file", "x"),
                     lambda: github.add_sub_issue(SLUG, "30", url),
                     lambda: github.repo_issues(SLUG),
                     lambda: github.open_features(SLUG, "feature")):
            with self.assertRaises(github.CmdError) as cm:
                call()
            self.assertIn("update gh to 2.94.0 or later", str(cm.exception))
            self.assertNotIn("unknown flag", str(cm.exception).lower())

    def test_pr_closing_reports_state_and_closing_references(self):    # T1 -> AC6
        body = self.body_file("Closes #7\n\nmore\n")
        gh_url = github.gh("pr", "create", "--repo", SLUG, "--head", "feat/7-x", "--base", "main",
                           "--title", "t", "--body-file", body).strip()
        n = gh_url.rsplit("/", 1)[1]
        self.assertEqual(github.pr_closing(SLUG, n),
                         {"state": "OPEN", "closes": [("7", SLUG)], "head": "", "unpassed": []})
        self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
        pr = self.state["repos"][SLUG]["prs"][0]
        pr.update(state="MERGED", closes=[])
        self.save()
        self.assertEqual(github.pr_closing(SLUG, n),
                         {"state": "MERGED", "closes": [], "head": "", "unpassed": []})
        self.assertIn(["pr", "view", n, "--repo", SLUG, "--json",
                       "state,headRefOid,statusCheckRollup,closingIssuesReferences"],
                      self.calls("pr", "view"))

    def test_pr_closing_reports_the_head_and_the_checks_that_have_not_passed(self):    # T2 -> AC2
        body = self.body_file("Closes #7\n")
        n = github.gh("pr", "create", "--repo", SLUG, "--head", "feat/7-x", "--base", "main",
                      "--title", "t", "--body-file", body).strip().rsplit("/", 1)[1]
        passed = [{"__typename": "CheckRun", "name": c, "status": "COMPLETED", "conclusion": c.upper()}
                  for c in ("success", "neutral", "skipped")]
        passed.append({"__typename": "StatusContext", "context": "legacy", "state": "SUCCESS"})
        unpassed = [{"__typename": "CheckRun", "name": "build", "status": "IN_PROGRESS", "conclusion": ""},
                    {"__typename": "CheckRun", "name": "lint", "status": "COMPLETED", "conclusion": "FAILURE"},
                    {"__typename": "StatusContext", "context": "deploy", "state": "PENDING"}]
        self.load()
        self.state["repos"][SLUG]["prs"][0]["checks"] = passed
        self.save()
        self.assertEqual(github.pr_closing(SLUG, n)["unpassed"], [])
        self.state["repos"][SLUG]["prs"][0]["checks"] = passed + unpassed
        self.save()
        self.assertEqual(github.pr_closing(SLUG, n)["unpassed"],
                         ["build (in_progress)", "lint (failure)", "deploy (pending)"])

    def test_squash_merge_asks_for_the_head_it_was_given(self):    # T1 -> AC1
        n = github.gh("pr", "create", "--repo", SLUG, "--head", "feat/7-x", "--base", "main",
                      "--title", "t", "--body-file", self.body_file("Closes #7\n")).strip().rsplit("/", 1)[1]
        git("switch", "-q", "-c", "feat/7-x", cwd=self.clone)
        (self.clone / "a.txt").write_text("a\n", encoding="utf-8")
        git("add", ".", cwd=self.clone)
        git("commit", "-q", "-m", "feat: a", cwd=self.clone)
        git("push", "-q", "origin", "feat/7-x", cwd=self.clone)
        sha = git("rev-parse", "HEAD", cwd=self.clone)
        with self.assertRaises(github.CmdError) as ctx:
            github.squash_merge(SLUG, n, "0" * 40)
        self.assertIn("Head branch was modified", str(ctx.exception))
        github.squash_merge(SLUG, n, sha)
        self.assertEqual(self.calls("pr", "merge")[-1],
                         ["pr", "merge", n, "--repo", SLUG, "--squash", "--match-head-commit", sha])
        git("fetch", "-q", "origin", cwd=self.clone)
        self.assertEqual(git("show", "origin/main:a.txt", cwd=self.clone), "a")


if __name__ == "__main__":
    unittest.main()


class DuoCase(GhCase):
    """A sealed-able design `duo-design-1-pair` with stories S1 (duo-a) and S2 (duo-b) in project `duo`."""
    NAME = "duo-design-1-pair"

    def setUp(self):
        super().setUp()
        self.path = self.vault / "Agent" / "Designs" / "duo" / f"{self.NAME}.md"
        self.snapshot("duo", self.build_duo)

    def build_duo(self):
        self.ok(self.cli("new", "project", "duo", "--repo", "acme/duo-a", "--repo", "acme/duo-b"))
        stories = ("### S1. Build it\nOutcome: x.\nDepends on: None\nRepo: duo-a\n\n"
                   "### S2. Ship it\nOutcome: y.\nDepends on: S1\nRepo: duo-b")
        body = ("# Pair\n\n## Problem\np\n\n## Goals\ng\n\n## Non-goals\nn\n\n## Ideas\nNone\n\n"
                "## Options considered\no\n\n## Chosen design\nc\n\n## Decisions\n"
                "### D1. Pair up\nDecision: d.\nWhy: w.\n\n## Stories\n" + stories + "\n\n## Open questions\nNone\n")
        self.path.parent.mkdir(parents=True)
        self.path.write_text(f"---\ntype: design\nproject: duo\nstatus: draft\ncreated: 2026-09-26\n---\n{body}",
                             encoding="utf-8", newline="\n")

    def seal(self):
        self.ok(self.cli("seal", "design", self.NAME))

    def add_issue(self, slug, n, state, body, labels=(), parent=None):
        self.load()
        repo = self.state["repos"].setdefault(slug, {
            "default_branch": "main", "head_sha": "a" * 40, "labels": [], "settings": {},
            "refuse_settings": False, "branches": [], "prs": [], "compare": {"total_commits": 0, "files": []},
            "issues": {}, "created_prs": [], "created_issues": [], "next_number": 20})
        repo["issues"][str(n)] = {"state": state, "title": "t", "created": CREATED, "body_edited": None,
                                  "renamed": None, "labels": list(labels), "body": body}
        if parent:
            repo["issues"][str(n)]["parent"] = parent
        self.save()

    def source(self, text):
        return ISSUE_BODY.replace("## Source\nsession", f"## Source\n{text}")



class DesignStoriesTest(DuoCase):  # T7 -> AC7
    def test_filed_in_the_second_repo_and_not_filed(self):
        self.seal()
        self.add_issue("acme/duo-b", 4, "CLOSED", self.source(f"[[{self.NAME}]] S1"))
        self.add_issue("acme/duo-a", 9, "OPEN", ISSUE_BODY.replace("## Problem\n", f"## Problem\nSee [[{self.NAME}]] S2\n"))
        out = self.ok(self.cli("design", "stories", self.NAME)).stdout
        self.assertIn("S1. Build it: acme/duo-b#4 (closed)", out)
        self.assertIn("S2. Ship it: not filed", out)
        self.assertIn("1 of 2 stories filed", out)

    def test_every_filed_story_and_a_prefix_is_not_a_match(self):
        self.seal()
        self.add_issue("acme/duo-a", 1, "OPEN", self.source(f"[[{self.NAME}]] S1"))
        self.add_issue("acme/duo-b", 2, "OPEN", self.source(f"[[{self.NAME}]] S2\n[[{self.NAME}-x]] S1"))
        out = self.ok(self.cli("design", "stories", self.NAME)).stdout
        self.assertIn("S1. Build it: acme/duo-a#1 (open)", out)
        self.assertIn("S2. Ship it: acme/duo-b#2 (open)", out)
        self.assertIn("2 of 2 stories filed", out)

    def test_reads_issues_from_github_each_time(self):
        self.seal()
        self.assertIn("not filed", self.ok(self.cli("design", "stories", self.NAME)).stdout)
        self.add_issue("acme/duo-a", 1, "OPEN", self.source(f"[[{self.NAME}]] S1"))
        self.assertIn("acme/duo-a#1", self.ok(self.cli("design", "stories", self.NAME)).stdout)
        self.assertTrue(self.calls("issue", "list"))

    def test_a_draft_design_is_refused(self):
        self.refused(self.cli("design", "stories", self.NAME), "draft")

    def test_a_project_without_repos(self):
        self.ok(self.cli("new", "project", "bare"))
        path = self.vault / "Agent" / "Designs" / "bare" / "bare-design-1-x.md"
        path.parent.mkdir(parents=True)
        text = self.path.read_text(encoding="utf-8").replace("project: duo", "project: bare").replace(
            "status: draft", "status: sealed\nsealed: 2026-09-27").replace("\nRepo: duo-a", "").replace("\nRepo: duo-b", "")
        path.write_text(text, encoding="utf-8", newline="\n")
        out = self.ok(self.cli("design", "stories", "bare-design-1-x")).stdout
        self.assertIn("S1. Build it: not filed", out)
        self.assertIn("has no repos yet", out)


class FeatureCreateTest(DuoCase):  # T2-T4 -> AC1-AC3
    def setUp(self):
        super().setUp()
        self.url_a = "https://github.com/acme/duo-a/issues"
        self.snapshot("feature-create", self.build)

    def build(self):
        self.seal()
        self.add_issue("acme/duo-a", 1, "OPEN", self.source(f"[[{self.NAME}]] S1"))
        self.add_issue("acme/duo-b", 2, "OPEN", self.source(f"[[{self.NAME}]] S2"),
                       parent="https://github.com/acme/duo-b/issues/9")
        self.add_issue("acme/duo-b", 9, "OPEN", ISSUE_BODY, labels=["feature"])

    def feature(self, *extra, source=None, repo="duo-a", body=None):
        text = body or self.source(source if source is not None else f"[[{self.NAME}]]")
        return self.cli("issue", "create", repo, "--title", "The pair", "--body-file", self.body_file(text),
                        *(extra or ("--feature", self.NAME)))

    def writes(self):
        self.load()
        return [c for c in self.state["calls"] if c[:2] in (["issue", "create"], ["label", "create"],
                                                              ["issue", "edit"])]

    def test_creates_the_labelled_feature_and_attaches_the_parentless_story(self):    # T2 -> AC1
        out = self.ok(self.feature()).stdout
        self.assertEqual(self.writes()[0][:2], ["label", "create"])
        made = self.load() and self.state["repos"]["acme/duo-a"]["created_issues"]
        self.assertEqual((made[0]["label"], made[0]["parent"]), ("feature", None))
        edits = self.calls("issue", "edit")
        self.assertEqual(edits, [["issue", "edit", "20", "--repo", "acme/duo-a", "--add-sub-issue",
                                  f"{self.url_a}/1"]])
        self.assertIn("S2 acme/duo-b#2 is already under https://github.com/acme/duo-b/issues/9", out)
        self.assertIn("left where it is", out)
        self.assertTrue(out.strip().endswith("https://github.com/acme/duo-a/issues/20"))

    def test_an_existing_label_is_not_created_again(self):
        self.load()
        self.state["repos"]["acme/duo-a"]["labels"].append("feature")
        self.save()
        self.ok(self.feature())
        self.assertEqual(self.calls("label", "create"), [])

    def refuses_without_writing(self, r, *words):
        self.refused(r, *words)
        self.assertEqual(self.writes(), [])

    def test_a_missing_design(self):    # T3 -> AC2
        self.refuses_without_writing(self.feature("--feature", "duo-design-9-none"), "no design")

    def test_a_draft_design(self):
        text = self.path.read_text(encoding="utf-8").replace("status: sealed", "status: draft")
        self.path.write_text(text, encoding="utf-8", newline="\n")
        self.refuses_without_writing(self.feature(), "draft")

    def test_a_repo_of_another_project(self):
        self.refuses_without_writing(self.feature(repo="widget"), "doesn't belong")

    def test_a_source_without_the_design_link(self):
        self.refuses_without_writing(self.feature(source="session"), f"[[{self.NAME}]]")

    def test_a_source_naming_a_story(self):
        self.refuses_without_writing(self.feature(source=f"[[{self.NAME}]] S1"), "names no story")
        self.refuses_without_writing(self.feature(source=f"[[{self.NAME}]]\n[[other-design-1-x]] S2"),
                                     "names no story")

    def test_a_closed_feature_in_the_other_repo(self):
        self.add_issue("acme/duo-b", 9, "CLOSED", self.source(f"[[{self.NAME}]]"), labels=["feature"])
        self.refuses_without_writing(self.feature(), "already has a feature", "acme/duo-b#9")

    def test_an_unlabelled_issue_with_that_source(self):
        self.add_issue("acme/duo-b", 9, "OPEN", self.source(f"[[{self.NAME}]]"))
        self.refuses_without_writing(self.feature(), "already has a feature")

    def test_idea_and_parent_are_refused(self):
        self.refuses_without_writing(self.feature("--feature", self.NAME, "--idea", "x"), "--idea")
        r = self.feature("--feature", self.NAME, "--parent", "9")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertEqual(self.writes(), [])

    def test_a_failed_attach_does_not_stop_the_rest(self):    # T4 -> AC3
        self.add_issue("acme/duo-b", 2, "OPEN", self.source(f"[[{self.NAME}]] S2"))
        self.load()
        self.state["refuse_sub_issue"] = {f"{self.url_a}/1": "Issue is already a sub-issue"}
        self.save()
        r = self.feature()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("Issue is already a sub-issue", r.stderr)
        self.assertIn(f"gh issue edit 20 --repo acme/duo-a --add-sub-issue {self.url_a}/1", r.stderr)
        self.assertIn("S2 acme/duo-b#2 attached to acme/duo-a#20", r.stdout)
        self.assertTrue(r.stdout.strip().endswith("https://github.com/acme/duo-a/issues/20"))


class ParentTest(DuoCase):  # T5 -> AC4
    def setUp(self):
        super().setUp()
        self.snapshot("parent", self.seal)

    def feature(self, n=30, source=None, state="OPEN", slug="acme/duo-a", label=True):
        body = self.source(source if source is not None else f"[[{self.NAME}]]")
        self.add_issue(slug, n, state, body, labels=["feature"] if label else [])

    def create(self, *parent, repo="duo-a", source="session"):
        return self.cli("issue", "create", repo, "--title", "A story", "--body-file",
                        self.body_file(self.source(source)), *(parent or ("--standalone",)))

    def created(self, slug="acme/duo-a"):
        self.load()
        return self.state["repos"].get(slug, {}).get("created_issues", [])

    def test_a_parent_in_the_same_repo_and_in_another(self):
        self.feature()
        self.ok(self.create("--parent", "30"))
        self.ok(self.create("--parent", "duo-a#30", repo="duo-b"))
        self.assertEqual(self.created()[0]["parent"], "https://github.com/acme/duo-a/issues/30")
        self.assertEqual(self.created("acme/duo-b")[0]["parent"], "https://github.com/acme/duo-a/issues/30")

    def test_no_parent_sends_no_flag(self):
        self.ok(self.create())
        self.assertIsNone(self.created()[0]["parent"])
        self.assertTrue(all("--parent" not in c for c in self.calls("issue", "create")))

    def test_promote_with_a_parent(self):
        self.feature()
        idea = self.idea("--project", "duo")
        self.ok(self.cli("idea", "promote", idea, "--repo", "duo-a", "--parent", "30",
                         "--body-file", self.body_file(self.source("session"))))
        self.assertEqual(self.created()[0]["parent"], "https://github.com/acme/duo-a/issues/30")
        self.assertIn("promoted", (self.vault / "Agent" / "Ideas" / f"{idea}").read_text(encoding="utf-8"))

    def refuses(self, r, *words):
        self.refused(r, *words)
        self.assertEqual(self.created(), [])
        self.assertEqual(self.created("acme/duo-b"), [])

    def test_unregistered_repo_and_another_project(self):
        self.feature()
        self.refuses(self.create("--parent", "nope#30"), "isn't registered")
        self.refuses(self.create("--parent", "widget#7"), "share no project")

    def test_a_closed_feature_and_an_unlabelled_issue(self):
        self.feature(30, state="CLOSED")
        self.feature(31, label=False)
        self.refuses(self.create("--parent", "30"), "closed")
        self.refuses(self.create("--parent", "31"), "isn't labelled")

    def test_a_malformed_parent(self):
        self.refuses(self.create("--parent", "x"), "issue number")

    def test_a_story_goes_under_its_own_feature(self):
        self.feature(30)
        self.feature(31, source="session")
        self.ok(self.create("--parent", "30", source=f"[[{self.NAME}]] S1"))
        self.assertEqual(len(self.created()), 1)
        self.refused(self.create("--parent", "31", source=f"[[{self.NAME}]] S1"),
                     "isn't the feature of", "acme/duo-a#30")
        self.assertEqual(len(self.created()), 1)

    def test_a_story_whose_design_has_no_feature(self):
        self.feature(31, source="session")
        self.refuses(self.create("--parent", "31", source=f"[[{self.NAME}]] S1"), "no feature yet",
                     f"--feature {self.NAME}")


class PlacementTest(DuoCase):  # T1, T2, T3 -> AC1, AC2, AC3 (#57)
    def create(self, *flags, source="session", repo="duo-a"):
        return self.cli("issue", "create", repo, "--title", "An issue", "--body-file",
                        self.body_file(self.source(source)), *flags)

    def writes(self):
        return [c for c in self.calls("issue") + self.calls("label") if c[1] in ("create", "edit")]

    def made(self):
        self.load()
        return self.state["repos"].get("acme/duo-a", {}).get("created_issues", [])

    def test_exactly_one_placement_flag_is_required(self):    # T1
        for flags, words in (((), ("--feature", "--parent", "--standalone", "--bug")),
                             (("--standalone", "--bug"), ("--standalone", "--bug")),
                             (("--parent", "30", "--standalone"), ("--parent", "--standalone"))):
            r = self.create(*flags)
            self.assertEqual(r.returncode, 2, r.stderr)
            for w in words:
                self.assertIn(w, r.stderr)
        self.assertEqual(self.writes(), [])

    def test_promote_needs_one_of_three_and_has_no_feature_flag(self):    # T1
        name = self.idea("--project", "duo")
        body = self.body_file(self.source("session"))
        r = self.cli("idea", "promote", name, "--repo", "duo-a", "--body-file", body)
        self.assertEqual(r.returncode, 2, r.stderr)
        for w in ("--parent", "--standalone", "--bug"):
            self.assertIn(w, r.stderr)
        r = self.cli("idea", "promote", name, "--repo", "duo-a", "--body-file", body, "--standalone", "--feature", "x")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("unrecognized", r.stderr)
        self.assertEqual(self.writes(), [])
        self.assertIn("status: open", (self.vault / "Agent" / "Ideas" / name).read_text(encoding="utf-8"))

    def test_standalone_and_bug_labels(self):    # T2
        self.ok(self.create("--standalone"))
        args = self.calls("issue", "create")[0]
        self.assertNotIn("--parent", args)
        self.assertNotIn("--label", args)
        self.ok(self.create("--bug"))
        args = self.calls("issue", "create")[1]
        self.assertEqual(args[args.index("--label") + 1], "bug")
        self.assertNotIn("--parent", args)
        self.assertEqual(len(self.calls("label", "create")), 1)
        self.ok(self.create("--bug"))
        self.assertEqual(len(self.calls("label", "create")), 1)    # exists now: not created again

    def test_with_an_idea_or_promote(self):    # T2
        for flag in ("--bug", "--standalone"):
            name = self.idea("--project", "duo")
            self.ok(self.cli("idea", "promote", name, "--repo", "duo-a", flag,
                             "--body-file", self.body_file(self.source("session"))))
            self.assertIn("status: promoted", (self.vault / "Agent" / "Ideas" / name).read_text(encoding="utf-8"))
        self.assertEqual([m["label"] for m in self.made()], ["bug", None])
        name = self.idea("--project", "duo")
        r = self.cli("issue", "create", "duo-a", "--title", "T", "--idea", name, "--bug",
                     "--body-file", self.body_file(self.source("session")))
        self.ok(r)
        self.assertEqual(self.made()[-1]["label"], "bug")

    def test_a_story_is_refused_without_a_parent(self):    # T3
        self.seal()
        for source in (f"[[{self.NAME}]] S1", "[[no-such-design]] S2"):
            for flag in ("--standalone", "--bug"):
                self.refused(self.create(flag, source=source), "--parent", "feature")
        self.assertEqual(self.writes(), [])
        self.add_issue("acme/duo-a", 30, "OPEN", self.source(f"[[{self.NAME}]]"), labels=["feature"])
        self.refused(self.create("--parent", "30", source="[[no-such-design]] S2"), "no-such-design")
        self.assertEqual(self.writes(), [])


class FeaturesTest(DuoCase):  # T6, T7 -> AC5, AC6
    def feature(self, slug, n, state="OPEN", summary=None, design=None):
        self.add_issue(slug, n, state, self.source(f"[[{design or self.NAME}]]"), labels=["feature"])
        if summary:
            self.state["repos"][slug]["issues"][str(n)]["sub_summary"] = summary
            self.save()

    def test_lists_the_open_features_of_a_project(self):    # T6
        self.feature("acme/duo-a", 30, summary={"total": 3, "completed": 2})
        self.feature("acme/duo-b", 31, summary={"total": 0, "completed": 0})
        self.feature("acme/duo-b", 32, state="CLOSED")
        self.add_issue("acme/duo-b", 33, "OPEN", ISSUE_BODY)
        out = self.ok(self.cli("features", "--project", "duo")).stdout.splitlines()
        self.assertEqual(out, [f"duo-a#30 t [[{self.NAME}]] (1 of 3 open)",
                               f"duo-b#31 t [[{self.NAME}]] (0 of 0 open)"])

    def test_without_a_project_every_repo_is_listed(self):
        self.feature("acme/duo-a", 30)
        self.feature(SLUG, 8)
        out = self.ok(self.cli("features")).stdout
        self.assertIn("duo-a#30", out)
        self.assertIn("widget#8", out)

    def test_none_and_an_unknown_project(self):
        self.assertEqual(self.ok(self.cli("features", "--project", "duo")).stdout.strip(), "no open features")
        self.refused(self.cli("features", "--project", "nope"), "does not exist")

    def test_design_stories_shows_the_feature_and_marks_strays(self):    # T7
        self.seal()
        self.feature("acme/duo-a", 30)
        url = "https://github.com/acme/duo-a/issues/30"
        self.add_issue("acme/duo-a", 1, "OPEN", self.source(f"[[{self.NAME}]] S1"), parent=url)
        self.add_issue("acme/duo-b", 2, "OPEN", self.source(f"[[{self.NAME}]] S2"),
                       parent="https://github.com/acme/duo-b/issues/9")
        self.add_issue("acme/duo-b", 9, "OPEN", ISSUE_BODY)
        out = self.ok(self.cli("design", "stories", self.NAME)).stdout
        self.assertIn("feature: acme/duo-a#30 (open)", out)
        self.assertIn("S1. Build it: acme/duo-a#1 (open)\n", out)
        self.assertIn("S2. Ship it: acme/duo-b#2 (open) [not under the feature]", out)

    def test_design_stories_without_a_feature(self):
        self.seal()
        out = self.ok(self.cli("design", "stories", self.NAME)).stdout
        self.assertIn("no feature yet", out)
        self.assertNotIn("not under", out)


class ClosesFeatureTest(DuoCase):  # T1-T3 -> AC1, AC5
    FEATURE = "https://github.com/acme/duo-a/issues/30"

    def finished(self):
        """A sealed design, its feature duo-a#30, S1 (duo-a#1, open) and S2 (duo-b#2, closed) under it."""
        self.seal()
        self.add_issue("acme/duo-a", 30, "OPEN", self.source(f"[[{self.NAME}]]"), labels=["feature"])
        self.add_issue("acme/duo-a", 1, "OPEN", self.source(f"[[{self.NAME}]] S1"), parent=self.FEATURE)
        self.add_issue("acme/duo-b", 2, "CLOSED", self.source(f"[[{self.NAME}]] S2"), parent=self.FEATURE)

    def says(self, n="1", repo="duo-a"):
        r = self.ok(self.cli("closes-feature", repo, n))
        return r.stdout.strip()

    def vault_files(self):
        return {p: p.read_bytes() for p in self.vault.rglob("*") if p.is_file()}

    def edit(self, slug, n, **changes):
        self.load()
        self.state["repos"][slug]["issues"][str(n)].update(changes)
        self.save()

    def test_names_the_feature_the_last_story_finishes(self):
        self.finished()
        before = self.vault_files()
        want = "sealing closes feature acme/duo-a#30: t"
        self.assertEqual(self.says(), want)
        self.edit("acme/duo-a", 1, state="CLOSED")
        self.assertEqual(self.says(), want)
        self.assertEqual(self.vault_files(), before)
        self.assertEqual(self.calls("issue", "close"), [])
        self.assertEqual(self.calls("issue", "edit"), [])

    def refusal(self, *words):
        out = self.says()
        self.assertTrue(out.startswith("sealing closes no feature:"), out)
        for w in words:
            self.assertIn(w, out)

    def test_an_unfiled_story(self):
        self.finished()
        self.edit("acme/duo-b", 2, body=ISSUE_BODY)
        self.refusal("S2")

    def test_another_open_sub_issue(self):
        self.finished()
        self.add_issue("acme/duo-b", 5, "OPEN", ISSUE_BODY, parent=self.FEATURE)
        self.refusal("acme/duo-b#5")

    def test_an_open_sub_issue_outside_the_project(self):
        self.finished()
        self.add_issue("acme/other", 6, "OPEN", ISSUE_BODY, parent=self.FEATURE)
        self.refusal("acme/other#6")

    def test_no_parent(self):
        self.finished()
        self.edit("acme/duo-a", 1, parent=None)
        self.refusal("no parent")

    def test_a_parent_that_is_not_a_feature(self):
        self.finished()
        self.edit("acme/duo-a", 30, labels=[])
        self.refusal("isn't labelled")

    def test_a_closed_feature(self):
        self.finished()
        self.edit("acme/duo-a", 30, state="CLOSED")
        self.refusal("closed")

    def test_a_design_not_in_the_vault(self):
        self.finished()
        self.edit("acme/duo-a", 30, body=self.source("[[duo-design-9-gone]]"))
        self.refusal("duo-design-9-gone", "isn't in the vault")

    def test_a_draft_design(self):
        self.seal()
        self.add_issue("acme/duo-a", 30, "OPEN", self.source(f"[[{self.NAME}]]"), labels=["feature"])
        self.add_issue("acme/duo-a", 1, "OPEN", self.source(f"[[{self.NAME}]] S1"), parent=self.FEATURE)
        text = self.path.read_text(encoding="utf-8").replace("status: sealed", "status: draft")
        self.path.write_text(text, encoding="utf-8", newline="\n")
        self.refusal("isn't sealed")

    def test_unknown_repo_and_bad_issue_are_refused(self):
        self.refused(self.cli("closes-feature", "nope", "1"), "nope")
        self.refused(self.cli("closes-feature", "duo-a", "abc"), "abc")

    def test_listed_in_help_and_the_docstring(self):
        self.assertIn("closes-feature", self.ok(self.cli("--help")).stdout)
        self.assertIn("closes-feature <repo> <issue>", (REPO / "agent_vault" / "vault.py").read_text(encoding="utf-8"))


class ScenarioCopyTest(GhCase):  # T1 -> AC3
    prep = SealRetroTest.prep
    build_prep = SealRetroTest.build_prep
    fill_retro = SealRetroTest.fill_retro
    record = SealRetroTest.record

    def assert_state_is_the_file(self):
        self.assertEqual(self.state, json.loads(self.state_path.read_text(encoding="utf-8")))

    def test_every_absolute_path_points_into_this_tests_folder(self):
        self.assertNotEqual(self.root, scenario().root)
        self.assertTrue(json.loads(self.cfg.read_text(encoding="utf-8"))["vault"].startswith(self.root.as_posix()))
        self.assert_state_is_the_file()
        self.assertEqual(Path(self.state["repos"][SLUG]["bare"]), self.bare)
        rule = git("config", "--get-regexp", r"^url\..*\.insteadof$", cwd=self.clone)
        self.assertIn(self.bare.as_posix(), rule)
        self.assertEqual(git("ls-remote", "origin", "main", cwd=self.clone).split()[0],
                         git("rev-parse", "HEAD", cwd=self.clone))

    def damaged_prep(self):
        self.prep()
        self.assert_state_is_the_file()
        self.assertEqual(len(self.state["repos"][SLUG]["created_prs"]), 1)
        self.assertTrue(self.retro.is_file())
        self.assertTrue((self.clone / "README.md").is_file())     # the earlier test's damage is not here
        (self.clone / "README.md").unlink()
        (self.vault / "Agent" / "Daily").mkdir(parents=True, exist_ok=True)

    def test_a_prepared_copy_has_its_state_and_the_pr_a(self):
        self.damaged_prep()

    def test_a_prepared_copy_has_its_state_and_the_pr_b(self):
        self.damaged_prep()

    def test_a_decision_added_before_prep_gets_the_full_prep(self):
        self.record("Old rule", "session")
        self.prep()
        self.assertEqual(len(list((self.vault / "Agent" / "Decisions" / "widgets").glob("*.md"))), 1)
        self.assertTrue(self.retro.is_file())
