"""The Claude Code hooks, run as subprocesses with simulated hook input (design §9).
`gh` is tests/fake_gh.py; nothing here touches GitHub."""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import time
import unittest
import uuid
from pathlib import Path

from tests.helpers import CODE, REPO, make_vault, run_py, tmpdir, write_config

HOOKS = CODE / "hooks"
CLI = CODE / "vault.py"
FAKE_GH = REPO / "tests" / "fake_gh.py"
SLUG = "me/widget"
V1_NAMES = ["Intake", "new note", "new daily", "Notes/", "_system"]   # AC6: guard messages are v2 only


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class HookCase(unittest.TestCase):
    """A vault with project `demo` (repo me/widget, plan and impl for issue 7), a clone of that
    repo on feat/7-thing, an unregistered clone, and a fake gh."""

    def setUp(self):
        self._t = tmpdir()
        self.addCleanup(self._t.cleanup)
        self.root = Path(self._t.name)
        self.vault = make_vault(self.root)
        self.cfg = write_config(self.root, self.vault)
        self.home = self.root / "home"
        (self.home / ".claude" / "agent-vault").mkdir(parents=True)
        self.gh_state = self.root / "gh.json"
        empty = self.root / "empty-gitconfig"
        empty.write_text("", encoding="utf-8")
        self.env = {"HOME": str(self.home), "USERPROFILE": str(self.home), "GIT_CONFIG_GLOBAL": str(empty),
                    "GIT_CONFIG_NOSYSTEM": "1", "AGENT_VAULT_GH": str(FAKE_GH),
                    "FAKE_GH_STATE": str(self.gh_state)}
        self.set_gh({"issues": {"7": {"state": "OPEN", "title": "T", "created": "2026-09-01T00:00:00Z",
                                      "body_edited": None, "renamed": None, "labels": []}}})
        self.sid = uuid.uuid4().hex
        self.repo = self.make_clone("widget", SLUG)
        git("checkout", "-q", "-b", "feat/7-thing", cwd=self.repo)
        self.other = self.make_clone("other", "me/other")
        self.cli("new", "project", "demo", "--repo", SLUG, "--purpose", "Build widgets.")
        self.cli("new", "plan", "widget", "7")

    # --- helpers ---
    def set_gh(self, repo_state: dict, **top):
        full = {"default_branch": "main", "head_sha": "a" * 40, "labels": [], "settings": {},
                "refuse_settings": False, "branches": [], "prs": [], "compare": {"total_commits": 0, "files": []},
                "issues": {}, "created_prs": [], "created_issues": [], "next_number": 20, **repo_state}
        self.gh_state.write_text(json.dumps({"repos": {SLUG: full}, "calls": [], **top}), encoding="utf-8")

    def make_clone(self, name: str, slug: str) -> Path:
        repo = self.root / name
        repo.mkdir()
        git("init", "-q", "-b", "main", cwd=repo)
        for k, v in (("user.name", "t"), ("user.email", "t@example.com"), ("commit.gpgsign", "false")):
            git("config", k, v, cwd=repo)
        git("remote", "add", "origin", f"https://github.com/{slug}.git", cwd=repo)
        (repo / "a.txt").write_text("a\n", encoding="utf-8")
        git("add", ".", cwd=repo)
        git("commit", "-q", "-m", "init", cwd=repo)
        return repo

    def cli(self, *args):
        r = run_py(CLI, list(args), config=self.cfg, env=self.env)
        self.assertEqual(r.returncode, 0, f"{args}: {r.stdout}{r.stderr}")
        return r

    def hook(self, name: str, data: dict, sid: str | None = None):
        return run_py(HOOKS / name, stdin={"session_id": sid or self.sid, **data}, config=self.cfg, env=self.env)

    def tool(self, hook, tool, path, cwd=None):
        key = "notebook_path" if tool == "NotebookEdit" else "file_path"
        return self.hook(hook, {"tool_name": tool, "cwd": str(cwd or self.root), "tool_input": {key: str(path)}})

    def start(self, cwd):
        r = self.hook("session_start.py", {"cwd": str(cwd)})
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def stop(self, active=False, cwd=None):
        return self.hook("stop_check.py", {"cwd": str(cwd or self.root), "stop_hook_active": active})

    def agent(self, rel: str) -> Path:
        return self.vault / "Agent" / rel

    def put(self, path: Path, text: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        return path

    def seal(self, path: Path):
        path.write_text(path.read_text(encoding="utf-8").replace("status: draft", "status: sealed")
                        .replace("status: open", "status: sealed"), encoding="utf-8", newline="\n")

    def tick(self):
        time.sleep(0.05)      # file times must move past the session start / the last change


class GuardTest(HookCase):  # T5 -> AC5, AC6, T12
    def blocked(self, path, tool="Write", cwd=None, says=""):
        r = self.tool("guard.py", tool, path, cwd)
        self.assertEqual(r.returncode, 2, f"{path}: {r.stdout}{r.stderr}")
        self.assertIn(says, r.stderr)
        for old in V1_NAMES:
            self.assertNotIn(old, r.stderr, f"{path}: message names {old}")
        return r.stderr

    def allowed(self, path, tool="Edit", cwd=None):
        r = self.tool("guard.py", tool, path, cwd)
        self.assertEqual(r.returncode, 0, f"{path}: {r.stderr}")

    def test_phone_side(self):
        self.put(self.vault / "Inbox" / "idea.md", "x")
        self.blocked(self.vault / "Inbox" / "idea.md", says="handoff pull")
        self.blocked(self.vault / "taxonomy.json", says="phone side")
        self.blocked(self.vault / "Inbox" / "new.md", says="phone side")

    def test_obsidian_config(self):
        self.blocked(self.vault / ".obsidian" / "app.json", says="Obsidian")
        self.blocked(self.vault / ".obsidian" / "nb.ipynb", tool="NotebookEdit", says="Obsidian")

    def test_installed_code_and_vault_claude_md(self):
        self.blocked(self.home / ".claude" / "agent-vault" / "vault.py", says="install.py")
        self.blocked(self.vault / "CLAUDE.md", says="install.py")

    def test_sealed_notes(self):
        for kind in ("plan", "impl"):
            self.seal(self.agent(f"Work/widget/widget-7-{kind}.md"))
            self.blocked(self.agent(f"Work/widget/widget-7-{kind}.md"), tool="Edit", says="sealed")
        self.put(self.agent("Work/widget/widget-7-retro.md"), "---\ntype: retro\nstatus: sealed\n---\n")
        self.blocked(self.agent("Work/widget/widget-7-retro.md"), tool="MultiEdit", says="sealed")

    def test_daily_notes_have_no_exceptions(self):
        today = self.agent("Daily/2026-01-05.md")
        self.put(today, "---\ntype: daily\n---\n")
        for path in (today, self.agent("Daily/2099-01-01.md")):
            self.blocked(path, says="log")
        self.blocked(self.agent("Daily/2026-01-05.md"), tool="Edit", says="the user")

    def test_new_notes_and_loose_files(self):
        msg = self.blocked(self.agent("Projects/new.md"), says="new project")
        self.assertIn("new plan", msg)
        self.blocked(self.agent("Ideas/2026-01-01-x.md"), says="idea add")
        self.blocked(self.agent("loose.md"), says="loose files")
        self.blocked(self.agent("loose.md"), tool="Edit", says="Projects/")

    def test_allowed_edits(self):
        self.allowed(self.agent("Work/widget/widget-7-impl.md"))
        self.allowed(self.agent("Work/widget/widget-7-plan.md"))           # a draft plan
        self.allowed(self.agent("Projects/demo.md"))
        self.allowed(self.vault / ".claude" / "settings.json", tool="Write")
        self.allowed(self.repo / "a.txt")                                   # outside the vault
        self.allowed(self.home / "elsewhere.txt", tool="Write")
        self.allowed(self.root / "elsewhere.txt", tool="Write", cwd=self.repo)

    def test_relative_paths_use_cwd(self):
        r = self.hook("guard.py", {"tool_name": "Edit", "cwd": str(self.vault / "Agent" / "Daily"),
                                   "tool_input": {"file_path": "2026-01-05.md"}})
        self.assertEqual(r.returncode, 2)
        r = self.hook("guard.py", {"tool_name": "Edit", "cwd": str(self.repo), "tool_input": {"file_path": "a.txt"}})
        self.assertEqual(r.returncode, 0)

    def test_applies_in_every_session(self):
        # decided by the target path, not the session's cwd: a repo or an unrelated folder
        for cwd in (self.repo, self.other, self.root):
            self.blocked(self.agent("Daily/2026-01-05.md"), cwd=cwd, says="log")

    def test_no_input_or_no_config(self):
        self.assertEqual(self.hook("guard.py", {"tool_name": "Write", "tool_input": {}}).returncode, 0)
        r = run_py(HOOKS / "guard.py", stdin="not json", config=self.root / "missing.json", env=self.env)
        self.assertEqual(r.returncode, 0)


class PostEditTest(HookCase):  # T6 -> AC7, T12
    def test_breaking_edit_blocks_with_errors(self):
        p = self.agent("Projects/demo.md")
        p.write_text(p.read_text(encoding="utf-8").replace("status: active", "status: bogus"),
                     encoding="utf-8", newline="\n")
        r = self.tool("post_edit.py", "Edit", p, cwd=self.repo)     # also from a repo session
        self.assertEqual(r.returncode, 2)
        self.assertIn("Projects/demo.md", r.stderr)
        self.assertIn("status", r.stderr)

    def test_valid_and_warning_only_notes_pass(self):
        self.assertEqual(self.tool("post_edit.py", "Edit", self.agent("Projects/demo.md")).returncode, 0)
        plan = self.agent("Work/widget/widget-7-plan.md")           # a fresh draft plan only warns
        self.assertEqual(lib_validate(self.cfg, plan)["errors"], [])
        self.assertTrue(lib_validate(self.cfg, plan)["warnings"])
        r = self.tool("post_edit.py", "Write", plan)
        self.assertEqual((r.returncode, r.stderr), (0, ""))

    def test_ignores_files_that_are_not_agent_notes(self):
        self.put(self.vault / "Inbox" / "x.md", "no frontmatter")
        for p in (self.vault / "Inbox" / "x.md", self.repo / "a.txt", self.agent("Ideas/gone.md")):
            self.assertEqual(self.tool("post_edit.py", "Write", p).returncode, 0)

    def test_daily_note_format_is_validated(self):
        self.cli("log", "did a thing", "--project", "demo")
        daily = next((self.vault / "Agent" / "Daily").glob("*.md"))
        daily.write_text(daily.read_text(encoding="utf-8") + "not a log line\n", encoding="utf-8", newline="\n")
        r = self.tool("post_edit.py", "Edit", daily)
        self.assertEqual(r.returncode, 2)
        self.assertIn("DAILY-LINE", r.stderr)


def lib_validate(cfg, path: Path) -> dict:
    code = ("import sys, json; sys.path.insert(0, sys.argv[1]); import lib; "
            "r = lib.validate_file(__import__('pathlib').Path(sys.argv[2])); "
            "print(json.dumps({'errors': r.errors, 'warnings': r.warnings}))")
    out = subprocess.run([sys.executable, "-c", code, str(CODE), str(path)], capture_output=True, text=True,
                         env={**__import__("os").environ, "AGENT_VAULT_CONFIG": str(cfg)})
    return json.loads(out.stdout)


class SessionStartTest(HookCase):
    def test_repo_session_context(self):  # T2 -> AC2
        self.cli("log", "first thing", "--project", "demo")
        self.cli("log", "unrelated", "--project", "demo")
        self.cli("idea", "add", "Cache it", "--project", "demo")
        self.cli("idea", "add", "Other idea")
        out = self.start(self.repo)
        self.assertIn("Build widgets.", out)                            # the project overview
        self.assertIn("feat/7-thing", out)
        self.assertIn("widget#7", out)
        self.assertIn("plan (draft)", out)
        self.assertIn("impl (open)", out)
        self.assertNotIn("retro (", out)
        self.assertIn("Stage: backlog", out)
        self.assertIn("first thing", out)
        self.assertIn("Open ideas for demo: 1", out)
        self.assertNotIn("Other idea", out)

    def test_repo_session_last_five_lines_only(self):
        for i in range(7):
            self.cli("log", f"line number {i}", "--project", "demo")
        out = self.start(self.repo)
        self.assertNotIn("line number 1", out)
        self.assertIn("line number 2", out)
        self.assertIn("line number 6", out)

    def test_repo_session_from_a_subfolder(self):
        (self.repo / "sub").mkdir()
        self.assertIn("Stage:", self.start(self.repo / "sub"))

    def test_branch_without_an_issue(self):
        git("checkout", "-q", "main", cwd=self.repo)
        out = self.start(self.repo)
        self.assertIn("isn't tied to an issue", out)
        self.assertIn("Build widgets.", out)

    def test_claudemd_lint_failures_shown(self):  # T2 -> AC8
        self.put(self.repo / "CLAUDE.md", "# widget\n\n## Layout\n- `missing.py`: nope.\n")
        git("add", "CLAUDE.md", cwd=self.repo)
        git("commit", "-q", "-m", "docs", cwd=self.repo)
        out = self.start(self.repo)
        self.assertIn("claudemd-lint found 1 problem(s):", out)
        self.assertIn("missing.py", out)

    def test_claudemd_lint_ok_and_stale_count(self):  # T2 -> AC8
        self.put(self.repo / "CLAUDE.md", "# widget\n\n## Purpose\nx.\n")
        git("add", "CLAUDE.md", cwd=self.repo)
        git("commit", "-q", "-m", "docs", cwd=self.repo)
        out = self.start(self.repo)
        self.assertIn("claudemd-lint: OK", out)
        self.assertNotIn("stale section", out)

    def test_claudemd_crash_gives_a_notice_and_never_blocks(self):  # T4 -> AC8
        script = self.root / "crash_claudemd.py"
        script.write_text(textwrap.dedent(f"""
            import sys
            sys.path[:0] = [{str(CODE)!r}, {str(HOOKS)!r}]
            import session_start, lib, claudemd
            def boom(*a, **k):
                raise RuntimeError("boom")
            claudemd.lint = boom
            lib.run_hook(session_start.main, "SessionStart", stream=sys.stdout)
        """), encoding="utf-8")
        r = run_py(script, stdin={"session_id": self.sid, "cwd": str(self.repo)}, config=self.cfg, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("claudemd-lint unavailable", r.stdout)
        self.assertIn("Build widgets.", r.stdout)                   # the rest still printed

    def test_silent_when_not_registered_or_not_git(self):
        self.assertEqual(self.start(self.other), "")
        self.assertEqual(self.start(self.root), "")
        self.assertEqual(self.hook("session_start.py", {}).stdout, "")

    def test_vault_session_context(self):  # T3 -> AC3
        self.cli("idea", "add", "An idea")
        for i in range(12):
            self.cli("log", f"vault line {i}", "--project", "demo")
        self.put(self.agent("stray.md"), "no frontmatter")          # something validate complains about
        out = self.start(self.vault)
        self.assertIn("[[demo]]", out)
        self.assertIn("Build widgets.", out)
        self.assertIn("Open ideas: 1", out)
        self.assertNotIn("vault line 1 ", out)
        self.assertIn("vault line 2", out)
        self.assertIn("vault line 11", out)
        self.assertIn("Agent/stray.md", out)
        self.assertRegex(out, r"Vault check found \d+ problem")
        self.assertTrue(list((self.vault / "Agent" / "Daily").glob("*.md")))

    def test_vault_session_creates_todays_note(self):
        self.assertFalse(list((self.vault / "Agent" / "Daily").glob("*.md")))
        self.start(self.vault)
        self.assertEqual(len(list((self.vault / "Agent" / "Daily").glob("*.md"))), 1)
        self.assertIn("Vault check: OK", self.start(self.vault / "Agent"))

    def test_hanging_gh_gives_a_notice_within_the_limit(self):  # T4 -> AC4
        self.set_gh({"issues": {}}, hang=True)
        t0 = time.monotonic()
        r = self.hook("session_start.py", {"cwd": str(self.repo)})
        self.assertLess(time.monotonic() - t0, 9)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Stage unavailable", r.stdout)
        self.assertIn("Build widgets.", r.stdout)                   # the rest still printed

    def test_missing_or_signed_out_gh_gives_a_notice(self):
        r = run_py(HOOKS / "session_start.py", stdin={"session_id": self.sid, "cwd": str(self.repo)},
                   config=self.cfg, env={**self.env, "AGENT_VAULT_GH": str(self.root / "nope.py")})
        self.assertEqual(r.returncode, 0)
        self.assertIn("Stage unavailable", r.stdout)

    def test_raising_helper_never_blocks_a_session(self):  # T4 -> AC4
        for name, patch in [("session_start", "lib.session_kind"), ("session_start", "lib.setup_io"),
                            ("guard", "lib.setup_io"), ("post_edit", "lib.setup_io"),
                            ("stop_check", "lib.setup_io")]:
            with self.subTest(hook=name, patched=patch):
                r = self.crashing(name, patch)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn("failed", r.stdout + r.stderr)

    def test_raising_repo_context_never_blocks_a_session(self):
        r = self.crashing("session_start", "vault.stage_of", cwd=self.repo)
        self.assertEqual(r.returncode, 0)
        self.assertIn("Stage unavailable", r.stdout)                # caught closer to the source
        r = self.crashing("session_start", "vault.all_ideas", cwd=self.repo)
        self.assertEqual(r.returncode, 0)
        self.assertIn("SessionStart hook failed", r.stdout)

    def crashing(self, module: str, target: str, cwd=None):
        """Run a hook's main() with one helper replaced by something that raises."""
        script = self.root / f"crash_{module}.py"
        mod, attr = target.split(".")
        name = {"session_start": "SessionStart", "guard": "guard", "post_edit": "PostToolUse",
                "stop_check": "Stop"}[module]
        stream = "sys.stdout" if module == "session_start" else "None"
        script.write_text(textwrap.dedent(f"""
            import sys
            sys.path[:0] = [{str(CODE)!r}, {str(HOOKS)!r}]
            import {module}, lib, vault
            def boom(*a, **k):
                raise RuntimeError("boom")
            setattr({mod}, {attr!r}, boom)
            lib.run_hook({module}.main, {name!r}, stream={stream})
        """), encoding="utf-8")
        data = {"session_id": self.sid, "cwd": str(cwd or self.vault),
                "tool_name": "Write", "tool_input": {"file_path": str(self.agent("Projects/demo.md"))}}
        return run_py(script, stdin=data, config=self.cfg, env=self.env)


class StopTest(HookCase):
    def test_vault_session_unlogged_work_blocks_once(self):  # T7 -> AC8
        self.start(self.vault)
        self.tick()
        self.put(self.agent("Ideas/2026-01-01-thing.md"), "x")
        self.tick()
        r = self.stop()
        self.assertEqual(r.returncode, 2)
        self.assertIn("Ideas/2026-01-01-thing.md", r.stderr)
        self.assertIn("vault.py", r.stderr)
        self.assertIn(" log ", r.stderr)
        self.assertEqual(self.stop(active=True).returncode, 0)      # blocks once

    def test_vault_session_logged_work_passes(self):
        self.start(self.vault)
        self.tick()
        self.put(self.agent("Ideas/2026-01-01-thing.md"), "x")
        self.tick()
        self.cli("log", "made a thing", "--project", "demo")
        self.assertEqual(self.stop().returncode, 0)
        self.tick()
        self.put(self.agent("Ideas/2026-01-01-thing.md"), "y")       # more work after the log: block again
        self.tick()
        self.assertEqual(self.stop().returncode, 2)

    def test_vault_session_without_changes_or_start_passes(self):
        self.start(self.vault)
        self.assertEqual(self.stop().returncode, 0)
        self.assertEqual(self.hook("stop_check.py", {"cwd": str(self.vault)}, sid="never-started").returncode, 0)

    def test_repo_session_unlogged_changes_block_once(self):  # T7 -> AC8
        self.start(self.repo)
        self.tick()
        (self.repo / "a.txt").write_text("changed\n", encoding="utf-8")
        r = self.stop(cwd=self.repo)
        self.assertEqual(r.returncode, 2)
        self.assertIn("a.txt", r.stderr)
        self.assertIn("--project demo", r.stderr)
        self.assertEqual(self.stop(active=True, cwd=self.repo).returncode, 0)

    def test_repo_session_new_files_and_commits_count(self):
        self.start(self.repo)
        self.tick()
        (self.repo / "new.py").write_text("x\n", encoding="utf-8")
        self.assertEqual(self.stop(cwd=self.repo).returncode, 2)
        git("add", ".", cwd=self.repo)
        git("commit", "-q", "-m", "work", cwd=self.repo)
        r = self.stop(cwd=self.repo)
        self.assertEqual(r.returncode, 2)
        self.assertIn("commits since session start", r.stderr)

    def test_repo_session_logged_or_clean_passes(self):
        (self.repo / "a.txt").write_text("dirty before the session\n", encoding="utf-8")
        self.start(self.repo)
        self.assertEqual(self.stop(cwd=self.repo).returncode, 0)     # pre-existing changes aren't ours
        self.tick()
        (self.repo / "a.txt").write_text("edited in the session\n", encoding="utf-8")
        self.tick()
        self.cli("log", "edited a.txt", "--project", "demo")
        self.assertEqual(self.stop(cwd=self.repo).returncode, 0)

    def test_repo_session_deleted_file_then_logged_passes(self):
        self.start(self.repo)
        self.tick()
        (self.repo / "a.txt").unlink()
        self.assertEqual(self.stop(cwd=self.repo).returncode, 2)
        self.tick()
        self.cli("log", "removed a.txt", "--project", "demo")
        self.assertEqual(self.stop(cwd=self.repo).returncode, 0)

    def test_repo_session_pipeline_event_counts_as_logged(self):
        self.start(self.repo)
        self.tick()
        daily = self.vault / "Agent" / "Daily" / (time.strftime("%Y-%m-%d") + ".md")
        self.cli("log", "seed", "--project", "demo")                # creates the note
        self.tick()
        (self.repo / "a.txt").write_text("work\n", encoding="utf-8")
        self.tick()
        self.assertEqual(self.stop(cwd=self.repo).returncode, 2)
        with open(daily, "a", encoding="utf-8", newline="\n") as f:
            f.write("- 12:00 widget#7 pr-opened\n")
        (self.repo / "a.txt").write_text("more work\n", encoding="utf-8")   # after the event line
        self.assertEqual(self.stop(cwd=self.repo).returncode, 0)

    def test_pipeline_event_for_another_repo_does_not_count(self):
        self.start(self.repo)
        self.tick()
        self.cli("log", "seed", "--project", "demo")
        daily = next((self.vault / "Agent" / "Daily").glob("*.md"))
        with open(daily, "a", encoding="utf-8", newline="\n") as f:
            f.write("- 12:00 gadget#7 pr-opened\n")
        self.tick()
        (self.repo / "a.txt").write_text("work\n", encoding="utf-8")
        self.assertEqual(self.stop(cwd=self.repo).returncode, 2)

    def test_other_sessions_are_not_checked(self):
        for cwd in (self.other, self.root):
            self.start(cwd)
            (Path(cwd) / "a.txt").write_text("changed\n", encoding="utf-8")
            self.assertEqual(self.stop(cwd=cwd).returncode, 0)

    def test_shell_written_sealed_and_closed_notes_are_named(self):  # T8 -> AC9
        self.seal(self.agent("Work/widget/widget-7-plan.md"))
        closed = self.put(self.agent("Daily/2026-01-05.md"),
                          "---\ntype: daily\ndate: 2026-01-05\n---\n# Monday\n\n## Log\n- 09:00 session — x\n")
        self.start(self.vault)
        self.tick()
        with open(self.agent("Work/widget/widget-7-plan.md"), "a", encoding="utf-8", newline="\n") as f:
            f.write("sneaky\n")
        closed.write_text(closed.read_text(encoding="utf-8") + "- 10:00 session — y\n", encoding="utf-8")
        self.cli("log", "logged", "--project", "demo")
        r = self.stop()
        self.assertEqual(r.returncode, 2)
        self.assertIn("Work/widget/widget-7-plan.md", r.stderr)
        self.assertIn("Daily/2026-01-05.md", r.stderr)
        self.assertIn("Tell the user", r.stderr)
        self.assertNotIn(time.strftime("%Y-%m-%d"), r.stderr)       # today's note isn't in the list
        self.assertEqual(self.stop(active=True).returncode, 0)

    def test_deleted_sealed_note_is_named(self):
        self.seal(self.agent("Work/widget/widget-7-impl.md"))
        self.start(self.repo)
        self.agent("Work/widget/widget-7-impl.md").unlink()
        r = self.stop(cwd=self.repo)
        self.assertEqual(r.returncode, 2)
        self.assertIn("widget-7-impl.md", r.stderr)

    def test_todays_daily_note_and_open_notes_may_change(self):
        self.start(self.vault)
        self.tick()
        self.cli("log", "today's line", "--project", "demo")
        self.assertEqual(self.stop().returncode, 0)

    def test_untouched_fingerprints_and_resume_keep_the_baseline(self):
        self.seal(self.agent("Work/widget/widget-7-plan.md"))
        self.start(self.vault)
        with open(self.agent("Work/widget/widget-7-plan.md"), "a", encoding="utf-8", newline="\n") as f:
            f.write("sneaky\n")
        self.start(self.vault)                                       # resume/compact fires SessionStart again
        self.assertEqual(self.stop().returncode, 2)

    def test_combined_message_and_stop_active(self):
        self.seal(self.agent("Work/widget/widget-7-plan.md"))
        self.start(self.vault)
        self.tick()
        self.put(self.agent("Ideas/2026-01-01-thing.md"), "x")
        with open(self.agent("Work/widget/widget-7-plan.md"), "a", encoding="utf-8", newline="\n") as f:
            f.write("sneaky\n")
        r = self.stop()
        self.assertEqual(r.returncode, 2)
        self.assertIn("widget-7-plan.md", r.stderr)
        self.assertIn("log this session's work", r.stderr)

    def test_bump_projects_is_gone_and_projects_are_untouched(self):  # T9 -> AC10
        self.assertNotIn("bump_projects", (HOOKS / "stop_check.py").read_text(encoding="utf-8"))
        before = self.agent("Projects/demo.md").read_bytes()
        self.start(self.vault)
        self.tick()
        self.put(self.agent("Ideas/2026-01-01-thing.md"), "x")
        self.stop()
        self.assertEqual(self.agent("Projects/demo.md").read_bytes(), before)


class TemplateTest(unittest.TestCase):
    def test_vault_guide_has_the_shell_write_rule(self):  # T11 -> AC12
        text = (REPO / "templates" / "vault-CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn("Never write vault files through the shell", text)
        self.assertIn("~/.claude/settings.json", text)
        self.assertNotIn("`.claude/settings.json` enforce", text)


if __name__ == "__main__":
    unittest.main()
