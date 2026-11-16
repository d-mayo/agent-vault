import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from tests.helpers import REPO, find_bash, make_vault, tmpdir

sys.path.insert(0, str(REPO))
import install  # noqa: E402

FOREIGN = {"type": "command", "command": "echo mine"}


def lib_marker() -> str:
    sys.path.insert(0, str(REPO / "agent_vault"))
    import lib
    return lib.HOOK_MARKER


def snapshot(*roots: Path) -> dict:
    return {str(p): p.read_bytes() for r in roots for p in sorted(r.rglob("*")) if p.is_file()}


class InstallTest(unittest.TestCase):
    def setUp(self):
        self._t = tmpdir()
        self.root = Path(self._t.name)
        self.home = self.root / "home"
        self.vault = make_vault(self.root)
        # v1 leftovers: code in the vault, v1 vault settings, plus a foreign user hook
        sysdir = self.vault / "Agent" / "_system"
        (sysdir / "hooks").mkdir(parents=True)
        (sysdir / "lib.py").write_text("# v1")
        (sysdir / "handoff-log.json").write_text('{"a.md": "2026-09-26"}\n')
        (self.vault / ".claude").mkdir()
        (self.vault / ".claude" / "settings.json").write_text(json.dumps({
            "permissions": {"allow": ["Bash(python Agent/_system/vault.py:*)", "Bash(ls:*)"]},
            "hooks": {"Stop": [{"hooks": [{"type": "command",
                      "command": 'python "$CLAUDE_PROJECT_DIR/Agent/_system/hooks/stop_check.py"'}]}]}}))
        (self.home / ".claude").mkdir(parents=True)
        (self.home / ".claude" / "settings.json").write_text(json.dumps({
            "model": "opus", "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [FOREIGN]}]}}))

    def tearDown(self):
        self._t.cleanup()

    def cli(self):
        inst = (self.home / ".claude" / "agent-vault").resolve()
        return f'{install.python_cmd()} "{(inst / "vault.py").as_posix()}"'

    def run_install(self):
        self.repo = self.root / "repo"
        (self.repo / ".git").mkdir(parents=True, exist_ok=True)
        return install.install(self.vault, self.home, repo=self.repo)

    def test_fresh_install(self):  # T1 -> AC1, AC4
        self.run_install()
        inst = self.home / ".claude" / "agent-vault"
        self.assertTrue((inst / "vault.py").exists())
        self.assertTrue((inst / "hooks" / "push_guard.py").exists())
        self.assertFalse(list(inst.rglob("__pycache__")))
        cfg = json.loads((self.home / ".claude" / "agent-vault.json").read_text())
        self.assertTrue(os.path.samefile(cfg["vault"], self.vault))          # however spelled
        self.assertNotIn(chr(92), cfg["vault"])        # recorded with forward slashes
        self.assertEqual(cfg["python"], Path(sys.executable).as_posix())        # T8 -> AC8 (#33)
        if find_bash():                    # the hook's own line reads it back from this file
            hook = (REPO / "agent_vault" / "githooks" / "pre-push").read_text(encoding="utf-8")
            read_line = next(ln.strip() for ln in hook.splitlines() if ln.strip().startswith("py=$(sed"))
            out = subprocess.run([find_bash(), "-c", f'config="$1"; {read_line}; printf %s "$py"', "bash",
                                  (self.home / ".claude" / "agent-vault.json").as_posix()],
                                 capture_output=True, text=True)
            self.assertEqual(out.stdout, cfg["python"])
        us = json.loads((self.home / ".claude" / "settings.json").read_text())
        ours = [h["command"] for g in sum(us["hooks"].values(), []) for h in g["hooks"]
                if "/.claude/agent-vault/hooks/" in h["command"]]
        self.assertEqual(sorted(Path(c.split('" "')[1].rstrip('"')).name for c in ours),
                         sorted(["session_start.py", "guard.py", "push_guard.py", "post_edit.py",
                                 "stop_check.py"]))
        self.assertEqual([a for a in us["permissions"]["allow"] if "/.claude/agent-vault/vault.py" in a],
                         [f"Bash({self.cli()}:*)"])
        vs = json.loads((self.vault / ".claude" / "settings.json").read_text())   # T1: nothing of ours left
        self.assertNotIn("hooks", vs)
        self.assertEqual(vs["permissions"]["allow"], ["Bash(ls:*)"])
        claude_md = (self.vault / "CLAUDE.md").read_text()
        self.assertIn("/.claude/agent-vault/vault.py", claude_md)
        self.assertNotIn("{{CLI}}", claude_md)
        self.assertIn("phone pull", claude_md)                           # T5 -> AC5
        self.assertNotIn("handoff pull", claude_md)
        # v1 migration: code gone, phone log kept
        self.assertFalse((self.vault / "Agent" / "_system").exists())
        self.assertIn("a.md", (self.vault / "Agent" / ".phone-log.json").read_text())
        self.assertFalse([p for p in self.vault.rglob("*.py")])

    def _drop_v1(self):
        shutil.rmtree(self.vault / "Agent" / "_system")

    def test_old_log_merges_into_phone_log(self):  # T4 -> AC4
        self._drop_v1()
        agent = self.vault / "Agent"
        (agent / ".handoff-log.json").write_text('{"a.md": "old", "c.md": "old"}\n')
        (agent / ".phone-log.json").write_text('{"a.md": "newer", "b.md": "x"}\n')
        report = self.run_install()
        self.assertFalse((agent / ".handoff-log.json").exists())
        self.assertEqual(json.loads((agent / ".phone-log.json").read_text()),
                         {"a.md": "newer", "b.md": "x", "c.md": "old"})
        self.assertTrue(any(".handoff-log.json" in line for line in report))
        before = snapshot(self.home, self.vault)
        self.run_install()
        self.assertEqual(before, snapshot(self.home, self.vault))

    def test_unreadable_logs_are_left_alone(self):  # T4 -> AC4
        self._drop_v1()
        agent = self.vault / "Agent"
        for bad_old in (True, False):
            with self.subTest(bad_old=bad_old):
                old, new = '{"a.md": 1}\n', '{"b.md": 2}\n'
                if bad_old:
                    old = "{not json"
                else:
                    new = "{not json"
                (agent / ".handoff-log.json").write_text(old)
                (agent / ".phone-log.json").write_text(new)
                report = self.run_install()
                self.assertTrue(any(line.startswith("warning:") and "unreadable" in line
                                    for line in report), report)
                self.assertEqual((agent / ".handoff-log.json").read_text(), old)
                self.assertEqual((agent / ".phone-log.json").read_text(), new)
                self.assertTrue((self.home / ".claude" / "agent-vault" / "vault.py").exists())

    def test_rerun_identical(self):  # T2 -> AC2
        self.run_install()
        before = snapshot(self.home, self.vault)
        self.run_install()
        self.assertEqual(before, snapshot(self.home, self.vault))
        us = json.loads((self.home / ".claude" / "settings.json").read_text())
        ours = [h for g in us["hooks"]["PreToolUse"] for h in g["hooks"] if "push_guard" in h["command"]]
        self.assertEqual(len(ours), 1)

    def test_guard_is_registered_for_bash_and_powershell(self):  # T6 -> AC8
        self.run_install()
        us = json.loads((self.home / ".claude" / "settings.json").read_text())
        groups = [g for g in us["hooks"]["PreToolUse"] if any("push_guard" in h["command"] for h in g["hooks"])]
        self.assertEqual(len(groups), 1)
        self.assertEqual(set(groups[0]["matcher"].split("|")), {"Bash", "PowerShell"})

    def test_preserves_foreign_entries(self):  # T3 -> AC3
        report = self.run_install()
        us = json.loads((self.home / ".claude" / "settings.json").read_text())
        self.assertEqual(us["model"], "opus")
        self.assertIn(FOREIGN, [h for g in us["hooks"]["PreToolUse"] for h in g["hooks"]])
        self.assertTrue((self.home / ".claude" / "settings.json.bak").exists())
        self.assertTrue(any("not managed by agent-vault" in r for r in report))

    def test_user_permissions_survive(self):  # AC3
        us = self.home / ".claude" / "settings.json"
        d = json.loads(us.read_text())
        mine = "Bash(python ~/.claude/agent-vault/vault.py:*)"
        d["permissions"] = {"allow": [mine]}
        us.write_text(json.dumps(d))
        self.run_install()
        self.run_install()
        allow = json.loads(us.read_text())["permissions"]["allow"]
        self.assertIn(mine, allow)
        self.assertEqual(len([a for a in allow if a.startswith('Bash("') and "/.claude/agent-vault/vault.py" in a]), 1)

    def test_no_vault_settings_created(self):
        (self.vault / ".claude" / "settings.json").unlink()
        self.run_install()
        self.assertFalse((self.vault / ".claude" / "settings.json").exists())

    def test_bom_settings(self):
        us = self.home / ".claude" / "settings.json"
        us.write_bytes(b"\xef\xbb\xbf" + us.read_bytes())
        self.run_install()
        self.assertEqual(json.loads(us.read_text())["model"], "opus")

    def test_foreign_vault_hook_kept(self):
        vsp = self.vault / ".claude" / "settings.json"
        d = json.loads(vsp.read_text())
        d["hooks"]["Stop"][0]["hooks"].append(FOREIGN)
        vsp.write_text(json.dumps(d))
        self.run_install()
        vs = json.loads(vsp.read_text())
        self.assertEqual([h for g in vs["hooks"]["Stop"] for h in g["hooks"]], [FOREIGN])

    def test_migrates_vault_level_entries(self):  # T1 -> AC1: a rerun over a previous v2 install
        self.run_install()
        inst = self.home / ".claude" / "agent-vault"
        vsp = self.vault / ".claude" / "settings.json"
        old = {"hooks": install.user_hooks(inst), "permissions": {"allow": [f"Bash({self.cli()}:*)", "Bash(ls:*)"]}}
        vsp.write_text(json.dumps(old))
        (self.home / ".claude" / "agent-vault.json").write_text(json.dumps(
            {"vault": self.vault.as_posix(), "managed_allow": [f"Bash({self.cli()}:*)"]}))
        self.run_install()
        vs = json.loads(vsp.read_text())
        self.assertNotIn("hooks", vs)
        self.assertEqual(vs["permissions"]["allow"], ["Bash(ls:*)"])
        us = json.loads((self.home / ".claude" / "settings.json").read_text())
        self.assertEqual(len([a for a in us["permissions"]["allow"] if "vault.py" in a]), 1)

    def test_git_hooks_path(self):  # AC5 (installer part)
        import subprocess
        repo = self.root / "gitrepo"
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        install.install(self.vault, self.home, repo=repo)
        out = subprocess.run(["git", "-C", str(repo), "config", "core.hooksPath"],
                             capture_output=True, text=True).stdout.strip()
        self.assertEqual(out, install.HOOKS_DIR)

    def git_clone_with_commits(self, n=2):
        clone = self.root / "srcclone"
        git = lambda *a: subprocess.run(["git", "-C", str(clone), *a], check=True,  # noqa: E731
                                        capture_output=True, text=True).stdout.strip()
        subprocess.run(["git", "init", "-q", str(clone)], check=True)
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "t")
        git("config", "commit.gpgsign", "false")
        shas = []
        for i in range(n):
            (clone / "f.txt").write_text(str(i))
            git("add", ".")
            git("commit", "-q", "-m", f"c{i}", "--date", f"2026-01-0{i + 1}T00:00:00")
            shas.append(git("rev-parse", "HEAD"))
        return clone, shas, git

    def test_records_source_and_commit(self):  # T1 -> AC1 (#98)
        clone, shas, git = self.git_clone_with_commits()
        install.install(self.vault, self.home, repo=clone)
        cfgp = self.home / ".claude" / "agent-vault.json"
        cfg = json.loads(cfgp.read_text())
        self.assertTrue(os.path.samefile(cfg["source"], clone))
        self.assertNotIn(chr(92), cfg["source"])        # recorded with forward slashes
        self.assertEqual(cfg["commit"], shas[1])
        self.assertEqual(cfg["commit_time"], int(git("log", "-1", "--format=%ct")))
        self.assertEqual(git("config", "core.hooksPath"), install.HOOKS_DIR)
        install.install(self.vault, self.home, repo=clone, commit=shas[0])
        cfg = json.loads(cfgp.read_text())
        self.assertEqual(cfg["commit"], shas[0])
        self.assertEqual(cfg["commit_time"], int(git("log", "-1", "--format=%ct", shas[0])))
        install.install(self.vault, self.home, repo=clone, commit="0" * 40)
        cfg = json.loads(cfgp.read_text())
        self.assertNotIn("commit", cfg)
        self.assertNotIn("commit_time", cfg)
        plain = self.root / "plain"
        plain.mkdir()
        install.install(self.vault, self.home, repo=plain)
        cfg = json.loads(cfgp.read_text())
        self.assertNotIn("commit", cfg)
        self.assertTrue(os.path.samefile(cfg["source"], plain))
        self.assertNotIn(chr(92), cfg["source"])        # recorded with forward slashes

    def test_commit_option_as_subprocess(self):  # T1 -> AC1 (#98)
        clone, shas, git = self.git_clone_with_commits()
        real = lambda: subprocess.run(["git", "-C", str(REPO), "config", "--get", "core.hooksPath"],  # noqa: E731
                                      capture_output=True, text=True).stdout
        before = real()
        out = subprocess.run([sys.executable, str(REPO / "install.py"), "--vault", str(self.vault),
                              "--home", str(self.home), "--repo", str(clone), "--commit", shas[0]],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        cfg = json.loads((self.home / ".claude" / "agent-vault.json").read_text())
        self.assertEqual(cfg["commit"], shas[0])
        self.assertTrue(os.path.samefile(cfg["source"], clone))
        self.assertNotIn(chr(92), cfg["source"])        # recorded with forward slashes
        self.assertEqual(real(), before)

    def test_shared_pre_push_hook(self):  # T9 -> AC9
        self.run_install()
        installed = self.home / ".claude" / "agent-vault" / "githooks" / "pre-push"
        source = REPO / "agent_vault" / "githooks" / "pre-push"
        self.assertEqual(installed.read_bytes(), source.read_bytes())
        self.assertIn(lib_marker(), source.read_text(encoding="utf-8"))
        self.assertNotIn(b"\r", source.read_bytes())
        self.assertFalse((REPO / ".githooks").exists())           # one source, no second copy

    def test_skill_install_respects_user_skills(self):
        src = self.root / "skills-src"
        (src / "plan-story").mkdir(parents=True)
        (src / "plan-story" / "SKILL.md").write_text("ours")
        (src / "mine").mkdir()
        (src / "mine" / "SKILL.md").write_text("ours")
        user_skill = self.home / ".claude" / "skills" / "mine"
        user_skill.mkdir(parents=True)
        (user_skill / "SKILL.md").write_text("user's own")
        old = install.REPO
        try:
            fake = self.root / "fakerepo"
            fake.mkdir()
            shutil.copytree(src, fake / "skills")
            install.REPO = fake
            report = []
            install.install_skills(self.home / ".claude", report)
        finally:
            install.REPO = old
        self.assertEqual((user_skill / "SKILL.md").read_text(), "user's own")
        self.assertTrue((self.home / ".claude" / "skills" / "plan-story" / "SKILL.md").exists())
        self.assertTrue(any("skipped skill mine" in r for r in report))

    def _tree(self, root: Path) -> set:
        return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()
                and "__pycache__" not in p.parts and p.suffix != ".pyc" and p.name != ".gitkeep"}

    def test_replaces_file_by_file(self):  # T1 -> AC1
        from unittest import mock
        self.run_install()
        claude = self.home / ".claude"
        inst = claude / "agent-vault"
        skill = next(p for p in (claude / "skills").iterdir() if (p / install.SKILL_MARKER).exists())
        (inst / "stale.py").write_text("old")
        (inst / "olddir").mkdir()
        (inst / "olddir" / "x.py").write_text("old")
        (skill / "stale.md").write_text("old")
        (inst / "vault.py").write_text("old version")      # a file both versions have, differing
        staging = claude / install.STAGING
        (staging / "junk").mkdir(parents=True)
        (staging / "junk" / "left.txt").write_text("interrupted")
        common = [(inst / p) for p in self._tree(REPO / "agent_vault")] +                  [(skill / p) for p in self._tree(REPO / "skills" / skill.name)]
        problems, deleted, staged_in_skills = [], [], []
        real_replace, real_remove, real_rmdir = os.replace, os.remove, os.rmdir

        def check(what):
            for f in common:
                if not f.exists():
                    problems.append(f"{what}: {f} missing")
            if any(install.STAGING in str(p) for p in (claude / "skills").rglob("*")):
                staged_in_skills.append(what)

        def replace(a, b):
            check(f"before replace {b}")
            real_replace(a, b)
            check(f"after replace {b}")

        def remove(p, *a, **k):
            check(f"before remove {p}")
            deleted.append(Path(p).name)
            real_remove(p, *a, **k)
            check(f"after remove {p}")

        def rmdir(p, *a, **k):
            check(f"before rmdir {p}")
            real_rmdir(p, *a, **k)
            check(f"after rmdir {p}")

        with mock.patch.object(install.os, "replace", replace),                 mock.patch.object(install.os, "remove", remove),                 mock.patch.object(install.os, "rmdir", rmdir):
            self.run_install()
        self.assertEqual(problems, [])
        self.assertEqual(staged_in_skills, [])
        self.assertNotIn(install.SKILL_MARKER, deleted)
        self.assertFalse((inst / "stale.py").exists())
        self.assertFalse((inst / "olddir" / "x.py").exists())
        self.assertFalse((skill / "stale.md").exists())
        self.assertTrue((skill / install.SKILL_MARKER).exists())
        self.assertEqual(self._tree(inst), self._tree(REPO / "agent_vault"))
        self.assertEqual((inst / "vault.py").read_bytes(), (REPO / "agent_vault" / "vault.py").read_bytes())
        self.assertFalse(staging.exists())
        self.assertFalse((inst / "junk").exists())

    def test_new_file_in_source_appears(self):  # T1 -> AC1
        fake = self.root / "fakerepo"
        shutil.copytree(REPO / "agent_vault", fake / "agent_vault")
        shutil.copytree(REPO / "skills", fake / "skills")
        shutil.copytree(REPO / "templates", fake / "templates")
        (fake / "agent_vault" / "newfile.py").write_text("new")
        old = install.REPO
        try:
            install.REPO = fake
            self.run_install()
        finally:
            install.REPO = old
        self.assertEqual((self.home / ".claude" / "agent-vault" / "newfile.py").read_text(), "new")

    def test_replace_retries_then_gives_up(self):  # T3 -> AC2
        from unittest import mock
        self.run_install()
        inst = self.home / ".claude" / "agent-vault"
        (inst / "vault.py").write_text("old version")
        (inst / "stale.py").write_text("old")
        real = os.replace
        calls = []

        def flaky(a, b):
            if Path(b).name == "vault.py" and os.path.samefile(Path(b).parent, inst) and len(calls) < 2:
                calls.append(b)
                raise PermissionError("in use")
            real(a, b)

        with mock.patch.object(install.os, "replace", flaky), mock.patch.object(install.time, "sleep"):
            self.run_install()
        self.assertEqual(len(calls), 2)
        self.assertNotEqual((inst / "vault.py").read_text(), "old version")

        (inst / "vault.py").write_text("old version")
        (inst / "stale.py").write_text("old")

        def stuck(a, b):
            if Path(b).name == "vault.py" and os.path.samefile(Path(b).parent, inst):
                raise PermissionError("in use")
            real(a, b)

        with mock.patch.object(install.os, "replace", stuck), mock.patch.object(install.time, "sleep"):
            with self.assertRaises(SystemExit) as cm:
                self.run_install()
        msg = str(cm.exception)
        self.assertIn("vault.py", msg)
        self.assertIn("install.py", msg)
        self.assertTrue((inst / "stale.py").exists())            # nothing deleted before the failure
        self.assertEqual((inst / "vault.py").read_text(), "old version")

    def _clone(self, name="clone"):
        clone = self.root / name
        subprocess.run(["git", "init", "-q", str(clone)], check=True)
        return clone

    def _hooks_path(self, clone):
        return subprocess.run(["git", "-C", str(clone), "config", "core.hooksPath"],
                              capture_output=True, text=True).stdout.strip()

    def test_uninstall(self):  # T4 -> AC3
        clone = self._clone()
        install.install(self.vault, self.home, repo=clone)
        claude = self.home / ".claude"
        user_skill = claude / "skills" / "mine"
        user_skill.mkdir(parents=True)
        (user_skill / "SKILL.md").write_text("user skill")
        (claude / install.STAGING / "x").mkdir(parents=True)
        us = json.loads((claude / "settings.json").read_text())
        us["permissions"]["allow"].append("Bash(ls:*)")
        (claude / "settings.json").write_text(json.dumps(us))
        notes = snapshot(self.vault / "Agent")
        guide = (self.vault / "CLAUDE.md").read_bytes()
        marked = sorted(p.name for p in (claude / "skills").iterdir() if (p / install.SKILL_MARKER).exists())
        self.assertTrue(marked)
        report = install.uninstall(self.home, repo=clone)
        self.assertFalse((claude / install.STAGING).exists())
        self.assertFalse((claude / "agent-vault").exists())
        self.assertFalse((claude / "agent-vault.json").exists())
        for name in marked:
            self.assertFalse((claude / "skills" / name).exists())
        self.assertTrue((user_skill / "SKILL.md").exists())
        text = (claude / "settings.json").read_text()
        self.assertNotIn("/.claude/agent-vault/", text)
        us = json.loads(text)
        self.assertEqual(us["model"], "opus")
        self.assertIn(FOREIGN, [h for g in us["hooks"]["PreToolUse"] for h in g["hooks"]])
        self.assertEqual(us["permissions"]["allow"], ["Bash(ls:*)"])
        self.assertTrue((claude / "settings.json.bak").exists())
        self.assertEqual(self._hooks_path(clone), "")
        self.assertEqual(guide, (self.vault / "CLAUDE.md").read_bytes())
        self.assertEqual(notes, snapshot(self.vault / "Agent"))
        joined = "\n".join(report)
        for needle in ["agent-vault", "agent-vault.json", "core.hooksPath", *marked]:
            self.assertIn(needle, joined)
        self.assertEqual(len([r for r in report if r.startswith("removed skill ")]), len(marked))

    def test_uninstall_vault_entries_and_other_hooks_path(self):  # T5 -> AC3
        clone = self._clone()
        install.install(self.vault, self.home, repo=clone)
        subprocess.run(["git", "-C", str(clone), "config", "core.hooksPath", "elsewhere"], check=True)
        inst = self.home / ".claude" / "agent-vault"
        vsp = self.vault / ".claude" / "settings.json"
        vsp.write_text(json.dumps({"hooks": install.user_hooks(inst), "permissions": {
            "allow": [f"Bash({self.cli()}:*)", "Bash(ls:*)"]}}))
        install.uninstall(self.home, repo=clone)
        vs = json.loads(vsp.read_text())
        self.assertNotIn("hooks", vs)
        self.assertEqual(vs["permissions"]["allow"], ["Bash(ls:*)"])
        self.assertEqual(self._hooks_path(clone), "elsewhere")

    def test_uninstall_when_nothing_installed(self):  # T5 -> AC3
        clone = self._clone()
        (self.home / ".claude" / "settings.json").write_text('{"model":"opus"}')
        before = snapshot(self.home, self.vault)
        report = install.uninstall(self.home, repo=clone)
        self.assertEqual(before, snapshot(self.home, self.vault))
        self.assertFalse((self.home / ".claude" / "settings.json.bak").exists())
        self.assertTrue(any("nothing was installed" in r for r in report))
        for extra in ([], ["--vault", str(self.vault)]):
            out = subprocess.run([sys.executable, str(REPO / "install.py"), "--uninstall",
                                  "--home", str(self.home), "--repo", str(clone), *extra],
                                 capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr)
        out = subprocess.run([sys.executable, str(REPO / "install.py"), "--home", str(self.home)],
                             capture_output=True, text=True)
        self.assertNotEqual(out.returncode, 0)

    def test_uninstall_unsets_hooks_path_when_nothing_else(self):  # T5 -> AC3
        clone = self._clone()
        subprocess.run(["git", "-C", str(clone), "config", "core.hooksPath", install.HOOKS_DIR], check=True)
        report = install.uninstall(self.home, repo=clone)
        self.assertEqual(self._hooks_path(clone), "")
        self.assertTrue(any("core.hooksPath" in r for r in report))

    def test_uninstall_report_ends_with_restart(self):  # T6 -> AC3
        install.install(self.vault, self.home, repo=self._clone())
        report = install.uninstall(self.home, repo=self._clone("other"))
        self.assertIn("restart", report[-1])
        self.assertIn("Claude sessions", report[-1])

    def test_docs_describe_file_by_file_and_uninstall(self):  # T7 -> AC4, AC5
        for text in (install.__doc__, (REPO / "README.md").read_text(encoding="utf-8"),
                     (REPO / "CLAUDE.md").read_text(encoding="utf-8")):
            self.assertIn("--uninstall", text)
            self.assertIn("file by file", text)

    def test_rejects_non_vault(self):
        with self.assertRaises(SystemExit):
            install.install(self.root / "home", self.home)


@unittest.skipUnless(find_bash(), "needs bash")
class LauncherTest(unittest.TestCase):
    def setUp(self):
        self._t = tmpdir()
        self.root = Path(self._t.name)
        self.inst = self.root / "agent-vault"
        (self.inst / "bin").mkdir(parents=True)
        shutil.copyfile(REPO / "agent_vault" / "bin" / "vault", self.inst / "bin" / "vault")
        (self.inst / "vault.py").write_text(
            "import sys\nfor a in sys.argv[1:]:\n    print('[' + a + ']')\nsys.exit(3)\n", encoding="utf-8")
        self.home = self.root / "home"
        self.home.mkdir()

    def tearDown(self):
        self._t.cleanup()

    def run_launcher(self, config, *args):
        env = dict(os.environ, HOME=str(self.home))
        env.pop("AGENT_VAULT_CONFIG", None)
        if config is not None:
            env["AGENT_VAULT_CONFIG"] = str(config)
        return subprocess.run([find_bash(), (self.inst / "bin" / "vault").as_posix(), *args],
                              capture_output=True, text=True, env=env)

    def config(self, python):
        cfg = self.root / "agent-vault.json"
        cfg.write_text(json.dumps({"vault": "x", "python": python}, indent=2) + "\n", encoding="utf-8")
        return cfg

    def test_passes_arguments_and_exit_code(self):  # T1 -> AC1
        out = self.run_launcher(self.config(Path(sys.executable).as_posix()), "status", "two words", "--x=1")
        self.assertEqual(out.returncode, 3)
        self.assertEqual(out.stdout.split(), ["[status]", "[two", "words]", "[--x=1]"])

    def test_missing_python(self):  # T2 -> AC2
        out = self.run_launcher(self.config((self.root / "nope" / "python").as_posix()), "status")
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("rerun install.py", out.stderr)
        self.assertIn("nope/python", out.stderr)

    def test_no_config(self):  # T2 -> AC2: the default config path, under an empty HOME
        out = self.run_launcher(None, "status")
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("not installed", out.stderr)
        self.assertIn("install.py", out.stderr)

    def test_installed_launcher_is_executable_and_lf(self):  # T1 -> AC1
        home = self.root / "inst-home"
        (home / ".claude").mkdir(parents=True)
        vault = make_vault(self.root)
        repo = self.root / "repo"
        (repo / ".git").mkdir(parents=True)
        install.install(vault, home, repo=repo)
        launcher = home / ".claude" / "agent-vault" / "bin" / "vault"
        self.assertTrue(launcher.is_file())
        self.assertNotIn(b"\r", launcher.read_bytes())
        if sys.platform != "win32":
            self.assertTrue(os.access(launcher, os.X_OK))


if __name__ == "__main__":
    unittest.main()
