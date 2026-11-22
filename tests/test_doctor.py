"""`vault.py doctor` (#92): a read-only check of the install, run as a subprocess against temp setups."""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from tests.helpers import CODE, REPO, make_vault, tmpdir

sys.path.insert(0, str(REPO))
sys.path.insert(0, str(CODE))
import doctor  # noqa: E402
import install  # noqa: E402

VAULT_PY = CODE / "vault.py"
MAIN_TIME = 1_700_000_000
SECRET = "ghp_SECRETTOKEN0123456789"
GH_STANDIN = '''import sys
args = sys.argv[1:]
log = {log!r}
if log:
    with open(log, "a", encoding="utf-8") as f:
        f.write(" ".join(args) + "\\n")
if args[:1] == ["--version"]:
    print("gh version {version} (2026-01-01)")
    sys.exit(0)
if args[:2] == ["auth", "token"]:
    print({token!r})
    print({token!r}, file=sys.stderr)
    sys.exit({code})
sys.exit(9)
'''


def git(cwd, *args, env=None):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True,
                          env={**os.environ, **(env or {})}).stdout.strip()


def make_clone(path: Path, origin: str | None, when: int = MAIN_TIME) -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "t@example.com")
    git(path, "config", "user.name", "t")
    git(path, "config", "commit.gpgsign", "false")
    if origin:
        git(path, "remote", "add", "origin", origin)
    (path / "f.txt").write_text("x", encoding="utf-8")
    git(path, "add", ".")
    stamp = f"{when} +0000"
    git(path, "commit", "-q", "-m", "c", env={"GIT_COMMITTER_DATE": stamp, "GIT_AUTHOR_DATE": stamp})
    return path


def listing(root: Path) -> dict:
    out = {}
    for p in sorted(root.rglob("*")):
        if ".git" in p.relative_to(root).parts[:-1] or p.name == ".git" and p.is_dir():
            continue
        if p.is_file():
            st = p.stat()
            out[p.relative_to(root).as_posix()] = (st.st_size, st.st_mtime_ns)
    return out


class DoctorBase(unittest.TestCase):
    def setUp(self):
        self._t = tmpdir()
        self.root = Path(self._t.name)
        self.addCleanup(self._t.cleanup)
        self.claude = self.root / "home" / ".claude"
        self.inst = self.claude / "agent-vault"
        (self.inst / "bin").mkdir(parents=True)
        (self.inst / "hooks").mkdir()
        (self.inst / "bin" / "hook").write_text("#!/bin/sh\n", encoding="utf-8")
        for scripts in doctor.EXPECTED_HOOKS.values():
            for script in scripts:
                (self.inst / "hooks" / script).write_text("pass\n", encoding="utf-8")
        self.config_path = self.claude / "agent-vault.json"
        self.settings_path = self.claude / "settings.json"
        self.vault = make_vault(self.root)
        projects = self.vault / "Agent" / "Projects"
        projects.mkdir(parents=True)
        (projects / "widgets.md").write_text(
            "---\ntype: project\nid: widgets\nstatus: active\nrepos: [acme/widget]\n---\n# widgets\n",
            encoding="utf-8")
        self.logs = self.root.parent / (self.root.name + "-logs")
        self.logs.mkdir()
        self.addCleanup(shutil.rmtree, self.logs, True)
        self.gh_log = self.logs / "gh.log"
        self.source = make_clone(self.root / "src" / "agent-vault", "https://github.com/acme/agent-vault.git")
        git(self.source, "config", "--local", "core.hooksPath", "agent_vault/githooks")
        self.widget = make_clone(self.root / "src" / "widget", "https://github.com/acme/widget.git")
        git(self.widget, "config", "--local", "core.hooksPath", (CODE / "githooks").as_posix())
        self.elsewhere = self.root / "elsewhere"
        self.elsewhere.mkdir()
        self.empty_path = self.root / "empty-path"
        self.empty_path.mkdir()
        self.config = {"vault": self.vault.as_posix(), "python": Path(sys.executable).as_posix(),
                       "gh": self.make_gh(), "git": Path(shutil.which("git")).as_posix(),
                       "source": self.source.as_posix(), "commit_time": MAIN_TIME,
                       "commit": "a" * 40}
        self.write_config()
        self.write_settings(json.loads(json.dumps({"hooks": install.user_hooks(self.inst)})))

    def make_gh(self, version="2.101.0", code=0, token=SECRET, name="gh.py", log=True) -> str:
        path = self.root / name
        path.write_text(GH_STANDIN.format(version=version, code=code, token=token,
                                          log=str(self.gh_log) if log else ""), encoding="utf-8")
        return path.as_posix()

    def write_config(self, **changes):
        cfg = {**self.config, **changes}
        cfg = {k: v for k, v in cfg.items() if v is not None}
        self.config_path.write_text(json.dumps(cfg), encoding="utf-8")

    def write_settings(self, settings):
        self.settings_path.write_text(json.dumps(settings), encoding="utf-8")

    def doctor(self, cwd=None, config=None, path=None):
        env = {k: v for k, v in os.environ.items() if k not in ("AGENT_VAULT_GH", "GIT_DIR", "GIT_WORK_TREE")}
        env["AGENT_VAULT_CONFIG"] = str(config or self.config_path)
        if path is not None:
            env["PATH"] = str(path)
        proc = subprocess.run([sys.executable, str(VAULT_PY), "doctor"], capture_output=True, text=True,
                              cwd=str(cwd or self.widget), env=env, encoding="utf-8")
        self.assertNotIn("Traceback", proc.stderr)
        return proc, proc.stdout.splitlines()

    def problems(self, lines, word=""):
        return [ln for ln in lines if ln.startswith("problem") and word in ln]

    def assert_one_problem(self, proc, lines, word, fix):
        found = self.problems(lines, word)
        self.assertTrue(found, lines)
        self.assertTrue(all("Fix:" in ln for ln in found), found)
        self.assertTrue(any(fix in ln for ln in found), found)
        self.assertEqual(proc.returncode, 1)


class HealthyTest(DoctorBase):
    def test_healthy_setup_is_all_ok(self):  # T1 -> AC1
        proc, lines = self.doctor()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(lines)
        for ln in lines:
            self.assertRegex(ln, r"^(ok|problem|skipped): ")
            self.assertTrue(ln.startswith("ok"), ln)

    def test_outside_a_clone_is_skipped_not_failed(self):  # T1 -> AC1
        proc, lines = self.doctor(cwd=self.elsewhere)
        self.assertEqual(proc.returncode, 0, lines)
        self.assertTrue(any(ln.startswith("skipped") and "git hooks" in ln for ln in lines), lines)
        self.assertFalse(self.problems(lines))

    def test_every_line_has_a_known_prefix_when_broken(self):  # T1 -> AC1
        self.write_config(python=None, source=None)
        proc, lines = self.doctor()
        self.assertEqual(proc.returncode, 1)
        for ln in lines:
            self.assertRegex(ln, r"^(ok|problem|skipped): ")
        for ln in self.problems(lines):
            self.assertIn("Fix:", ln)


class ConfigTest(DoctorBase):
    def test_missing_config(self):  # T2 -> AC2
        proc, lines = self.doctor(config=self.root / "nope" / "agent-vault.json")
        self.assert_one_problem(proc, lines, "config", "install.py")

    def test_config_is_not_json(self):  # T2 -> AC2
        self.config_path.write_text("{not json", encoding="utf-8")
        proc, lines = self.doctor()
        self.assert_one_problem(proc, lines, "config", "install.py")

    def test_vault_without_obsidian_folder(self):  # T2 -> AC2
        self.write_config(vault=(self.root / "not-a-vault").as_posix())
        proc, lines = self.doctor()
        self.assert_one_problem(proc, lines, ".obsidian", "install.py --vault")

    def test_python_field_missing(self):  # T2 -> AC2
        self.write_config(python=None)
        proc, lines = self.doctor()
        self.assert_one_problem(proc, lines, "python", "install.py")

    def test_python_file_gone(self):  # T2 -> AC2
        self.write_config(python=(self.root / "gone" / "python.exe").as_posix())
        proc, lines = self.doctor()
        self.assert_one_problem(proc, lines, "no longer exists", "install.py")


class ToolsTest(DoctorBase):
    def test_recorded_gh_gone(self):  # T3 -> AC3
        self.write_config(gh=(self.root / "gone-gh.py").as_posix())
        proc, lines = self.doctor()
        self.assert_one_problem(proc, lines, "recorded path", "install.py")

    def test_recorded_git_gone(self):  # T3 -> AC3
        self.write_config(git=(self.root / "gone" / "git.exe").as_posix())
        proc, lines = self.doctor()
        self.assert_one_problem(proc, lines, "git: the recorded path", "install.py")

    def test_no_gh_anywhere(self):  # T3 -> AC3
        self.write_config(gh=None)
        proc, lines = self.doctor(path=self.empty_path)
        self.assert_one_problem(proc, lines, "gh:", "install gh")

    def test_no_git_anywhere_skips_the_git_checks(self):  # T3 -> AC3
        self.write_config(git=None)
        proc, lines = self.doctor(path=self.empty_path)
        self.assert_one_problem(proc, lines, "git: not found", "install git")
        self.assertTrue(any(ln.startswith("skipped") and "git hooks" in ln for ln in lines), lines)
        self.assertTrue(any(ln.startswith("skipped") and "up to date" in ln for ln in lines), lines)
        self.assertFalse(self.problems(lines, "up to date"))

    def test_old_gh(self):  # T3 -> AC3
        self.write_config(gh=self.make_gh(version="2.93.0", name="old.py"))
        proc, lines = self.doctor()
        self.assert_one_problem(proc, lines, "2.93.0", "update gh")

    def test_not_logged_in(self):  # T3 -> AC3
        self.write_config(gh=self.make_gh(code=1, name="out.py"))
        proc, lines = self.doctor()
        self.assert_one_problem(proc, lines, "not logged in", "gh auth login")

    def test_token_never_printed(self):  # T3 -> AC3
        for code in (0, 1):
            with self.subTest(code=code):
                self.write_config(gh=self.make_gh(code=code, name=f"t{code}.py"))
                proc, _ = self.doctor()
                self.assertNotIn(SECRET, proc.stdout + proc.stderr)


class HooksTest(DoctorBase):
    def settings(self):
        return json.loads(self.settings_path.read_text(encoding="utf-8"))

    def test_missing_entry(self):  # T4 -> AC4
        s = self.settings()
        s["hooks"]["PostToolUse"] = []
        self.write_settings(s)
        proc, lines = self.doctor()
        self.assert_one_problem(proc, lines, "post_edit.py", "install.py")

    def test_missing_launcher(self):  # T4 -> AC4
        (self.inst / "bin" / "hook").unlink()
        proc, lines = self.doctor()
        self.assert_one_problem(proc, lines, "launcher", "install.py")

    def test_missing_script(self):  # T4 -> AC4
        (self.inst / "hooks" / "guard.py").unlink()
        proc, lines = self.doctor()
        self.assert_one_problem(proc, lines, "guard.py", "install.py")

    def test_leftover_v1_entry(self):  # T4 -> AC4
        s = self.settings()
        s["hooks"]["Stop"].append({"hooks": [{"type": "command", "command": "python Agent/_system/hooks/stop.py"}]})
        self.write_settings(s)
        proc, lines = self.doctor()
        self.assert_one_problem(proc, lines, "v1", "install.py")

    def test_unreadable_settings(self):  # T4 -> AC4
        self.settings_path.write_text("{oops", encoding="utf-8")
        proc, lines = self.doctor()
        self.assert_one_problem(proc, lines, "unreadable", "install.py")

    def test_missing_settings(self):  # T4 -> AC4
        self.settings_path.unlink()
        proc, lines = self.doctor()
        self.assert_one_problem(proc, lines, "settings.json", "install.py")

    def test_foreign_hooks_are_left_alone(self):  # T4 -> AC4
        s = self.settings()
        s["hooks"]["Stop"].append({"hooks": [{"type": "command", "command": "echo hello"}]})
        self.write_settings(s)
        proc, lines = self.doctor()
        self.assertEqual(proc.returncode, 0, lines)

    def test_expected_hooks_equal_the_installers(self):  # T4 -> AC4
        written = {}
        for event, groups in install.user_hooks(self.inst).items():
            for g in groups:
                for h in g["hooks"]:
                    written.setdefault(event, []).append(shlex.split(h["command"])[1])
        self.assertEqual({e: sorted(s) for e, s in written.items()},
                         {e: sorted(s) for e, s in doctor.EXPECTED_HOOKS.items()})
        self.assertEqual(install.MARK, doctor.INSTALL_MARK)
        self.assertEqual(install.LEGACY_HOOK_MARK, doctor.LEGACY_MARK)
        self.assertEqual(install.HOOKS_DIR, doctor.SOURCE_HOOKS)


class GitHooksTest(DoctorBase):
    def test_registered_clone_without_hooks_path(self):  # T5 -> AC5
        git(self.widget, "config", "--local", "--unset", "core.hooksPath")
        proc, lines = self.doctor()
        self.assert_one_problem(proc, lines, "no core.hooksPath", "repo-init")

    def test_registered_clone_with_a_foreign_path(self):  # T5 -> AC5
        foreign = self.root / "other-hooks"
        foreign.mkdir()
        (foreign / "pre-push").write_text("#!/bin/sh\n", encoding="utf-8")
        git(self.widget, "config", "--local", "core.hooksPath", foreign.as_posix())
        proc, lines = self.doctor()
        self.assert_one_problem(proc, lines, "isn't agent-vault's", "by hand")

    def test_shared_path_is_ok(self):  # T5 -> AC5
        proc, lines = self.doctor()
        self.assertTrue(any(ln.startswith("ok") and "installed hooks" in ln for ln in lines), lines)

    def test_source_clone_without_the_hooks_folder(self):  # T5 -> AC5
        git(self.source, "config", "--local", "core.hooksPath", "elsewhere")
        proc, lines = self.doctor()
        self.assert_one_problem(proc, lines, "source clone", "install.py")

    def test_running_in_the_source_clone_checks_it_once(self):  # T5 -> AC5
        proc, lines = self.doctor(cwd=self.source)
        self.assertEqual(proc.returncode, 0, lines)
        mine = [ln for ln in lines if "source clone" in ln and "git hooks" in ln]
        self.assertEqual(len(mine), 1, lines)
        self.assertFalse([ln for ln in lines if "installed hooks" in ln or "isn't listed" in ln], lines)
        git(self.source, "config", "--local", "--unset", "core.hooksPath")
        proc, lines = self.doctor(cwd=self.source)
        self.assertEqual(len(self.problems(lines, "source clone")), 1, lines)

    def test_unregistered_clone_is_skipped(self):  # T5 -> AC5
        other = make_clone(self.root / "src" / "other", "https://github.com/acme/other.git")
        proc, lines = self.doctor(cwd=other)
        self.assertEqual(proc.returncode, 0, lines)
        self.assertTrue(any(ln.startswith("skipped") and "isn't listed" in ln for ln in lines), lines)

    def test_no_usable_vault_is_skipped(self):  # T5 -> AC5
        self.write_config(vault=(self.root / "none").as_posix())
        proc, lines = self.doctor()
        self.assertTrue(any(ln.startswith("skipped") and "no usable vault" in ln for ln in lines), lines)

    def test_says_other_clones_are_not_checked(self):  # T5 -> AC5
        _, lines = self.doctor()
        self.assertTrue(any("other clones aren't checked" in ln for ln in lines), lines)


class OutOfDateTest(DoctorBase):
    def test_main_newer_than_the_install(self):  # T6 -> AC6
        self.write_config(commit_time=MAIN_TIME - 10)
        proc, lines = self.doctor()
        self.assert_one_problem(proc, lines, "newer than the installed copy", "new session")
        self.assertIn("install.py", " ".join(self.problems(lines)))

    def test_equal_time_is_up_to_date(self):  # T6 -> AC6
        proc, lines = self.doctor()
        self.assertTrue(any(ln.startswith("ok") and "up to date" in ln for ln in lines), lines)

    def test_no_source(self):  # T6 -> AC6
        self.write_config(source=None)
        proc, lines = self.doctor()
        self.assert_one_problem(proc, lines, "no source", "install.py")

    def test_source_missing(self):  # T6 -> AC6
        self.write_config(source=(self.root / "gone-clone").as_posix())
        proc, lines = self.doctor()
        self.assert_one_problem(proc, lines, "missing", "install.py")

    def test_source_without_main(self):  # T6 -> AC6
        git(self.source, "branch", "-m", "main", "trunk")
        proc, lines = self.doctor()
        self.assert_one_problem(proc, lines, "main", "install.py")


class ReadOnlyTest(DoctorBase):
    def snapshot(self):
        configs = {c.name: git(c, "config", "--local", "--list") for c in (self.source, self.widget)}
        return listing(self.root), configs

    def test_a_run_with_problems_changes_nothing(self):  # T7 -> AC7
        self.write_config(commit_time=MAIN_TIME - 10)           # out of date, so selfupdate would install
        git(self.widget, "config", "--local", "--unset", "core.hooksPath")
        s = json.loads(self.settings_path.read_text(encoding="utf-8"))
        s["hooks"]["Stop"] = []
        self.write_settings(s)
        before = self.snapshot()
        proc, lines = self.doctor()
        self.assertEqual(proc.returncode, 1)
        self.assertGreaterEqual(len(self.problems(lines)), 3, lines)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(json.loads(self.config_path.read_text(encoding="utf-8"))["commit"], "a" * 40)
        self.assertFalse((self.claude / "agent-vault-history.git").exists())

    def test_only_version_and_auth_token_run_gh(self):  # T7 -> AC7
        self.doctor()
        calls = self.gh_log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(sorted(set(calls)), ["--version", "auth token"])

    def test_no_pycache_is_left_in_the_code(self):  # T7 -> AC7
        before = {p for p in CODE.rglob("__pycache__")}
        self.doctor()
        self.assertEqual({p for p in CODE.rglob("__pycache__")}, before)


if __name__ == "__main__":
    unittest.main()
