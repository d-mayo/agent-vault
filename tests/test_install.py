import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import unittest
from functools import partial
from pathlib import Path
from unittest import mock

from tests.helpers import REPO, find_bash, make_vault, skip_install_prereqs, tmpdir

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
        env = mock.patch.dict(os.environ, {install.TEST_PREREQS_ENV: "1"})   # these tests don't test the check
        env.start()
        self.addCleanup(env.stop)
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
        return "~/.claude/agent-vault/bin/vault"

    def old_cli(self):                  # what an install before #44 recorded
        inst = (self.home / ".claude" / "agent-vault").resolve()
        return f'{install.python_cmd()} "{(inst / "vault.py").as_posix()}"'

    def run_install(self):
        self.repo = self.root / "repo"
        (self.repo / ".git").mkdir(parents=True, exist_ok=True)
        return install.install(self.vault, self.home, repo=self.repo)

    def our_hook_commands(self):
        us = json.loads((self.home / ".claude" / "settings.json").read_text())
        return [(g, h["command"]) for ev in us["hooks"].values() for g in ev for h in g["hooks"]
                if "/.claude/agent-vault/" in h["command"]]

    def test_hooks_call_the_launcher(self):  # T2 -> AC2
        self.run_install()
        launcher = self.home / ".claude" / "agent-vault" / "bin" / "hook"
        cmds = [c for _, c in self.our_hook_commands()]
        self.assertEqual(len(cmds), 5)
        for c in cmds:
            self.assertTrue(c.startswith('"'), c)
            quoted, _, _rest = c[1:].partition('" ')       # the temp path may be spelled differently
            self.assertTrue(os.path.samefile(quoted, launcher), c)
            self.assertNotIn(Path(sys.executable).as_posix(), c)
            self.assertEqual(c.endswith(" --blocking"), "guard.py" in c, c)
        self.assertEqual(sum(c.endswith(" --blocking") for c in cmds), 2)

    def test_reinstall_replaces_python_path_hooks_keeps_foreign(self):  # T2 -> AC2
        self.run_install()
        us_path = self.home / ".claude" / "settings.json"
        us = json.loads(us_path.read_text())
        old = f'{install.python_cmd()} "{(self.home / ".claude" / "agent-vault" / "hooks" / "stop_check.py").as_posix()}"'
        us["hooks"]["Stop"].append({"hooks": [{"type": "command", "command": old}]})
        us_path.write_text(json.dumps(us))
        self.run_install()
        cmds = [h["command"] for ev in json.loads(us_path.read_text())["hooks"].values()
                for g in ev for h in g["hooks"]]
        self.assertNotIn(old, cmds)
        self.assertIn(FOREIGN["command"], cmds)
        self.assertEqual(len([c for c in cmds if "/.claude/agent-vault/" in c]), 5)

    @unittest.skipUnless(find_bash(), "needs bash")
    def test_installed_hook_commands_run(self):  # T2 -> AC2: each command, end to end
        self.run_install()
        env = dict(os.environ, HOME=str(self.home),
                   AGENT_VAULT_CONFIG=str(self.home / ".claude" / "agent-vault.json"))
        for _, cmd in self.our_hook_commands():
            out = subprocess.run([find_bash(), "-c", cmd], input="{}", capture_output=True, text=True, env=env)
            self.assertEqual(out.returncode, 0, (cmd, out.stderr))

    def test_readme_and_warning_say_what_a_deleted_python_does(self):  # T5 -> AC5
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        self.assertNotIn("Hooks run with the same Python", readme)
        self.assertIn("install.py", readme.split("Hooks run with")[1].split("\n\n")[0])
        self.assertIn("rerun", (REPO / "install.py").read_text(encoding="utf-8").split("virtualenv")[1][:400])

    def test_docs_mention_force_guide(self):  # T7 -> docs step
        for name in ("README.md", "CLAUDE.md"):
            self.assertIn("--force-guide", (REPO / name).read_text(encoding="utf-8"), name)

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
                if "/.claude/agent-vault/bin/hook" in h["command"]]
        self.assertEqual(sorted(c.split('" ')[1].split()[0] for c in ours),
                         sorted(["session_start.py", "guard.py", "push_guard.py", "post_edit.py",
                                 "stop_check.py"]))
        self.assertEqual([a for a in us["permissions"]["allow"] if "agent-vault" in a],
                         [f"Bash({self.cli()}:*)"])
        self.assertEqual(cfg["managed_allow"], [f"Bash({self.cli()}:*)"])
        vs = json.loads((self.vault / ".claude" / "settings.json").read_text())   # T1: nothing of ours left
        self.assertNotIn("hooks", vs)
        self.assertEqual(vs["permissions"]["allow"], ["Bash(ls:*)"])
        claude_md = (self.vault / "CLAUDE.md").read_text()
        self.assertIn("~/.claude/agent-vault/bin/vault", claude_md)
        self.assertNotIn("vault.py", claude_md)
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
        self.assertEqual([a for a in allow if "agent-vault/bin/vault" in a], [f"Bash({self.cli()}:*)"])

    def test_old_absolute_rule_is_replaced(self):  # T3 -> AC3
        self.run_install()
        us = self.home / ".claude" / "settings.json"
        d = json.loads(us.read_text())
        d["permissions"]["allow"] = [f"Bash({self.old_cli()}:*)", "Bash(ls:*)"]
        us.write_text(json.dumps(d))
        cfgp = self.home / ".claude" / "agent-vault.json"
        cfg = json.loads(cfgp.read_text())
        cfg["managed_allow"] = [f"Bash({self.old_cli()}:*)"]
        cfgp.write_text(json.dumps(cfg))
        self.run_install()
        allow = json.loads(us.read_text())["permissions"]["allow"]
        self.assertEqual([a for a in allow if "agent-vault" in a], [f"Bash({self.cli()}:*)"])
        self.assertIn("Bash(ls:*)", allow)
        self.assertEqual(json.loads(cfgp.read_text())["managed_allow"], [f"Bash({self.cli()}:*)"])

    def test_guide_is_the_same_with_another_python(self):  # T5 -> AC5
        from unittest import mock
        clone, shas, git = self.git_clone_with_commits()
        install.install(self.vault, self.home, repo=clone)
        first = (self.vault / "CLAUDE.md").read_bytes()
        with mock.patch.object(sys, "executable", str(self.root / "other" / "python.exe")):
            install.install(self.vault, self.home, repo=clone)
        self.assertEqual((self.vault / "CLAUDE.md").read_bytes(), first)
        self.assertTrue(first.startswith(b"<!-- agent-vault "))
        self.assertIn(b"~/.claude/agent-vault/bin/vault", first)

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
        old = {"hooks": install.user_hooks(inst), "permissions": {"allow": [f"Bash({self.old_cli()}:*)", "Bash(ls:*)"]}}
        vsp.write_text(json.dumps(old))
        (self.home / ".claude" / "agent-vault.json").write_text(json.dumps(
            {"vault": self.vault.as_posix(), "managed_allow": [f"Bash({self.old_cli()}:*)"]}))
        self.run_install()
        vs = json.loads(vsp.read_text())
        self.assertNotIn("hooks", vs)
        self.assertEqual(vs["permissions"]["allow"], ["Bash(ls:*)"])
        us = json.loads((self.home / ".claude" / "settings.json").read_text())
        self.assertEqual(len([a for a in us["permissions"]["allow"] if "agent-vault" in a]), 1)

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
            when = f"2026-01-0{i + 1}T00:00:00"
            subprocess.run(["git", "-C", str(clone), "commit", "-q", "-m", f"c{i}", "--date", when],
                           check=True, capture_output=True,
                           env={**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when})
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

    def guide(self):
        return (self.vault / "CLAUDE.md").read_bytes()

    def marker(self, git, sha):
        return f"<!-- agent-vault {git('log', '-1', '--format=%ct', sha)} {sha} -->\n".encode()

    def held(self, report):
        return [ln for ln in report if "warning: the vault guide is from" in ln]

    def test_guide_marker_line(self):  # T1 -> AC1
        clone, shas, git = self.git_clone_with_commits()
        install.install(self.vault, self.home, repo=clone)
        self.assertEqual(len(shas[1]), 40)
        self.assertTrue(self.guide().startswith(self.marker(git, shas[1]) + b"# Vault guide"))
        install.install(self.vault, self.home, repo=clone, commit=shas[0], force_guide=True)
        self.assertTrue(self.guide().startswith(self.marker(git, shas[0]) + b"# Vault guide"))
        self.assertNotIn(b"{{CLI}}", self.guide())

    def test_older_install_keeps_newer_guide(self):  # T2 -> AC2
        clone, shas, git = self.git_clone_with_commits()
        install.install(self.vault, self.home, repo=clone)
        newer = self.guide()
        report = install.install(self.vault, self.home, repo=clone, commit=shas[0])
        self.assertEqual(self.guide(), newer)
        warning = self.held(report)
        self.assertEqual(len(warning), 1, report)
        self.assertIn("a newer agent-vault", warning[0])
        self.assertIn(shas[1], warning[0])
        self.assertIn("git pull", warning[0])
        self.assertIn("--force-guide", warning[0])
        cfg = json.loads((self.home / ".claude" / "agent-vault.json").read_text())
        self.assertEqual(cfg["commit"], shas[0])
        self.assertTrue((self.home / ".claude" / "agent-vault" / "vault.py").is_file())

    def test_newer_install_rewrites_older_guide(self):  # T3 -> AC3
        clone, shas, git = self.git_clone_with_commits()
        install.install(self.vault, self.home, repo=clone, commit=shas[0])
        old = self.guide()
        report = install.install(self.vault, self.home, repo=clone)
        self.assertNotEqual(self.guide(), old)
        self.assertTrue(self.guide().startswith(self.marker(git, shas[1])))
        self.assertEqual(self.held(report), [])
        again = self.guide()
        install.install(self.vault, self.home, repo=clone)       # the same commit: the same bytes
        self.assertEqual(self.guide(), again)

    def test_force_guide_rewrites_from_older(self):  # T3 -> AC3
        clone, shas, git = self.git_clone_with_commits()
        install.install(self.vault, self.home, repo=clone)
        report = install.install(self.vault, self.home, repo=clone, commit=shas[0], force_guide=True)
        self.assertTrue(self.guide().startswith(self.marker(git, shas[0])))
        self.assertEqual(self.held(report), [])
        out = subprocess.run([sys.executable, str(REPO / "install.py"), "--vault", str(self.vault),
                              "--home", str(self.home), "--repo", str(clone), "--commit", shas[1],
                              "--force-guide"], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertTrue(self.guide().startswith(self.marker(git, shas[1])))

    def test_unmarked_and_missing_guide_written_by_older_install(self):  # T3 -> AC3
        clone, shas, git = self.git_clone_with_commits()
        self.assertFalse((self.vault / "CLAUDE.md").exists())      # no guide at all
        install.install(self.vault, self.home, repo=clone, commit=shas[0])
        self.assertTrue(self.guide().startswith(self.marker(git, shas[0])))
        (self.vault / "CLAUDE.md").write_text("# Vault guide\nold, no marker\n", encoding="utf-8")
        install.install(self.vault, self.home, repo=clone, commit=shas[0])
        self.assertTrue(self.guide().startswith(self.marker(git, shas[0])))
        self.assertNotIn(b"old, no marker", self.guide())

    def test_no_commit_info(self):  # T4 -> AC4
        clone, shas, git = self.git_clone_with_commits()
        plain = self.root / "plain"
        plain.mkdir()
        install.install(self.vault, self.home, repo=clone)
        marked = self.guide()
        for kwargs in ({"repo": clone, "commit": "0" * 40}, {"repo": plain}):
            report = install.install(self.vault, self.home, **kwargs)
            self.assertEqual(self.guide(), marked)
            self.assertEqual(len(self.held(report)), 1, report)
            install.install(self.vault, self.home, force_guide=True, **kwargs)
            self.assertEqual(self.guide().decode().splitlines()[0], "# Vault guide")
            (self.vault / "CLAUDE.md").write_bytes(marked)
        (self.vault / "CLAUDE.md").write_text("# Vault guide\nunmarked\n", encoding="utf-8")
        report = install.install(self.vault, self.home, repo=plain)
        self.assertEqual(self.held(report), [])
        self.assertNotIn(b"unmarked", self.guide())
        self.assertEqual(self.guide().decode().splitlines()[0], "# Vault guide")

    def test_unparsable_marker_is_kept(self):  # T4 -> AC4
        clone, shas, git = self.git_clone_with_commits()
        junk = "<!-- agent-vault garbage -->\n# Vault guide\n"
        (self.vault / "CLAUDE.md").write_text(junk, encoding="utf-8")
        report = install.install(self.vault, self.home, repo=clone)
        self.assertEqual((self.vault / "CLAUDE.md").read_text(encoding="utf-8"), junk)
        self.assertEqual(len(self.held(report)), 1, report)
        install.install(self.vault, self.home, repo=clone, force_guide=True)
        self.assertTrue(self.guide().startswith(self.marker(git, shas[1])))

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
        skip_install_prereqs(self)
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


@unittest.skipUnless(find_bash(), "needs bash")
class HookLauncherTest(unittest.TestCase):
    def setUp(self):
        self._t = tmpdir()
        self.root = Path(self._t.name)
        self.inst = self.root / "agent-vault"
        (self.inst / "bin").mkdir(parents=True)
        (self.inst / "hooks").mkdir()
        shutil.copyfile(REPO / "agent_vault" / "bin" / "hook", self.inst / "bin" / "hook")
        (self.inst / "hooks" / "x.py").write_text(
            "import sys\nprint('stdin=' + sys.stdin.read())\nprint('argv=' + repr(sys.argv[1:]))\nsys.exit(3)\n",
            encoding="utf-8")
        (self.inst / "hooks" / "session_start.py").write_text("print('started')\n", encoding="utf-8")
        self.home = self.root / "home"
        self.home.mkdir()

    def tearDown(self):
        self._t.cleanup()

    def run_hook(self, config, *args, stdin="{}"):
        env = dict(os.environ, HOME=str(self.home))
        env.pop("AGENT_VAULT_CONFIG", None)
        if config is not None:
            env["AGENT_VAULT_CONFIG"] = str(config)
        return subprocess.run([find_bash(), (self.inst / "bin" / "hook").as_posix(), *args],
                              capture_output=True, text=True, env=env, input=stdin)

    def config(self, python, field=True):
        cfg = self.root / "agent-vault.json"
        data = {"vault": "x"}
        if field:
            data["python"] = python
        cfg.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return cfg

    def test_pass_through(self):  # T1 -> AC1, T4 -> AC4
        cfg = self.config(Path(sys.executable).as_posix())
        for extra in ([], ["--blocking"]):
            out = self.run_hook(cfg, "x.py", *extra, stdin='{"a": 1}')
            self.assertEqual(out.returncode, 3)
            self.assertIn('stdin={"a": 1}', out.stdout)
            self.assertIn("argv=[]", out.stdout)       # --blocking is the launcher's own

    def test_python_gone(self):  # T3 -> AC3
        for cfg in (self.config((self.root / "nope" / "python").as_posix()), self.config("", field=False)):
            blocked = self.run_hook(cfg, "x.py", "--blocking")
            self.assertEqual(blocked.returncode, 2)
            self.assertIn("rerun install.py", blocked.stderr)
            soft = self.run_hook(cfg, "x.py")
            self.assertEqual(soft.returncode, 1)
            self.assertIn("rerun install.py", soft.stderr)
            self.assertEqual(soft.stdout, "")

    def test_python_gone_with_a_large_payload(self):  # R1
        big = "x" * (2 * 1024 * 1024)
        cfg = self.config((self.root / "nope" / "python").as_posix())
        for args, code in ((["x.py", "--blocking"], 2), (["x.py"], 1), (["session_start.py"], 0)):
            out = self.run_hook(cfg, *args, stdin=big)
            self.assertEqual(out.returncode, code, args)
            self.assertIn("rerun install.py", out.stderr + out.stdout)

    def test_session_start_reports_in_hook_json(self):  # T3 -> AC3
        for missing in ("nope/python", 'a dir/with "quote"/python'):
            out = self.run_hook(self.config((self.root / missing).as_posix()), "session_start.py")
            self.assertEqual(out.returncode, 0)
            data = json.loads(out.stdout)
            self.assertIn("rerun install.py", data["systemMessage"])
            self.assertIn(missing.split('"')[0], data["systemMessage"])   # the config spells a quote escaped
            self.assertEqual(data["hookSpecificOutput"]["hookEventName"], "SessionStart")
            self.assertEqual(data["hookSpecificOutput"]["additionalContext"], data["systemMessage"])

    def test_no_config(self):  # T4 -> AC4: the default config path, under an empty HOME
        for args in (["x.py"], ["x.py", "--blocking"], ["session_start.py"]):
            out = self.run_hook(None, *args)
            self.assertEqual((out.returncode, out.stdout, out.stderr), (0, "", ""))

    def test_installed_launcher_is_executable_and_lf(self):  # T1 -> AC1
        skip_install_prereqs(self)
        home = self.root / "inst-home"
        (home / ".claude").mkdir(parents=True)
        vault = make_vault(self.root)
        repo = self.root / "repo"
        (repo / ".git").mkdir(parents=True)
        install.install(vault, home, repo=repo)
        launcher = home / ".claude" / "agent-vault" / "bin" / "hook"
        self.assertTrue(launcher.is_file())
        self.assertNotIn(b"\r", launcher.read_bytes())
        if sys.platform != "win32":
            self.assertTrue(os.access(launcher, os.X_OK))


if __name__ == "__main__":
    unittest.main()


GOOD_VERSION = "gh version 2.94.0 (2026-01-01)\nhttps://github.com/cli/cli/releases/tag/v2.94.0"
SECRET = "ghp_SECRETTOKENVALUE"


def fake_which(**found):
    return lambda name: found.get(name)


def fake_run(version=GOOD_VERSION, token_rc=0, calls=None):
    def run(argv, **kw):
        if calls is not None:
            calls.append(list(argv))
        if argv[-1] == "--version":
            return subprocess.CompletedProcess(argv, 0, stdout=version + "\n", stderr="")
        if argv[-2:] == ["auth", "token"]:
            return subprocess.CompletedProcess(argv, token_rc, stdout=SECRET + "\n", stderr=SECRET)
        raise AssertionError(argv)
    return run


ALL = {"git": "/usr/bin/git", "gh": "/usr/bin/gh", "ruff": "/usr/bin/ruff"}


def posix(path) -> str:
    return Path(os.path.abspath(path)).as_posix()


class PrerequisiteTest(unittest.TestCase):
    """install.check_prerequisites with injected lookups; no test depends on this machine's tools."""

    def setUp(self):
        self._t = tmpdir()
        self.root = Path(self._t.name)
        self.addCleanup(self._t.cleanup)
        self.home = self.root / "home"
        (self.home / ".claude").mkdir(parents=True)
        self.vault = make_vault(self.root)
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(install.TEST_PREREQS_ENV, None)

    def listing(self):
        return sorted(str(p.relative_to(self.root)) + (":" + p.read_bytes().hex() if p.is_file() else "")
                      for p in self.root.rglob("*"))

    def check(self, which=ALL, version=None, run=None, recorded=None):
        return install.check_prerequisites(recorded, which=fake_which(**which), run=run or fake_run(),
                                           version=version)

    def install_with(self, which=ALL, version=None, run=None, repo=None):
        fake = partial(install.check_prerequisites, which=fake_which(**which), run=run or fake_run(),
                       version=version)
        with mock.patch.object(install, "check_prerequisites", fake):
            return install.install(self.vault, self.home, repo=repo or self.root / "repo")

    def refuse(self, **kwargs):
        """Run install() with the injected lookups; it must exit and change nothing."""
        before = self.listing()
        with self.assertRaises(SystemExit) as cm:
            self.install_with(**kwargs)
        self.assertEqual(self.listing(), before)
        self.assertNotIn(SECRET, str(cm.exception))
        return str(cm.exception)

    def test_all_good(self):  # T1 -> AC1
        tools, problems, warnings = self.check()
        self.assertEqual(problems, [])
        self.assertEqual(warnings, [])
        self.assertEqual(tools, {"git": posix("/usr/bin/git"), "gh": posix("/usr/bin/gh")})

    def test_each_failure_alone_refuses(self):  # T1 -> AC1
        no_git = {k: v for k, v in ALL.items() if k != "git"}
        no_gh = {k: v for k, v in ALL.items() if k != "gh"}
        cases = [
            ({"version": (3, 8, 18)}, "Python 3.9"),
            ({"which": no_git}, "install git"),
            ({"which": no_gh}, "https://cli.github.com"),
            ({"run": fake_run("gh version 2.93.0 (2025-12-01)")}, "update gh from https://cli.github.com"),
            ({"run": fake_run(token_rc=1)}, "gh auth login"),
        ]
        for kwargs, fix in cases:
            with self.subTest(fix=fix):
                msg = self.refuse(**kwargs)
                self.assertIn(fix, msg)
                self.assertEqual(len(msg.splitlines()), 1)
                self.assertEqual(msg.count("; "), 0, msg)       # only that failure

    def test_all_failures_named_on_one_line(self):  # T1 -> AC1
        msg = self.refuse(which={}, version=(3, 8, 0))
        for fix in ("Python 3.9", "install git", "https://cli.github.com"):
            self.assertIn(fix, msg)
        self.assertEqual(len(msg.splitlines()), 1)
        msg = self.refuse(version=(3, 8, 0), run=fake_run("gh version 2.1.0", token_rc=1))
        for fix in ("Python 3.9", "older than 2.94.0", "gh auth login"):
            self.assertIn(fix, msg)
        self.assertEqual(len(msg.splitlines()), 1)

    def test_ruff_missing_only_warns(self):  # T2 -> AC2
        which = {k: v for k, v in ALL.items() if k != "ruff"}
        tools, problems, warnings = self.check(which=which)
        self.assertEqual(problems, [])
        self.assertTrue(any("ruff" in w for w in warnings))
        report = self.install_with(which=which)
        self.assertTrue(any(line.startswith("warning: ruff") for line in report))
        self.assertTrue((self.home / ".claude" / "agent-vault.json").exists())

    def test_passing_check_records_both_paths(self):  # T3 -> AC3
        self.install_with()
        cfg = json.loads((self.home / ".claude" / "agent-vault.json").read_text())
        self.assertEqual(cfg["git"], posix("/usr/bin/git"))
        self.assertEqual(cfg["gh"], posix("/usr/bin/gh"))
        self.assertNotIn(chr(92), cfg["git"] + cfg["gh"])

    def test_recorded_paths_used_only_when_path_lacks_the_tool(self):  # T3 -> AC3
        old_gh, old_git = self.root / "old-gh", self.root / "old-git"
        old_gh.write_text("x")
        old_git.write_text("x")
        recorded = {"gh": old_gh.as_posix(), "git": old_git.as_posix()}
        tools, problems, _ = self.check(which={}, recorded=recorded)
        self.assertEqual(problems, [])
        self.assertEqual(tools, recorded)
        tools, problems, _ = self.check(recorded=recorded)      # PATH wins
        self.assertEqual(tools["gh"], posix("/usr/bin/gh"))
        gone = {"gh": (self.root / "nope").as_posix(), "git": (self.root / "nope2").as_posix()}
        _, problems, _ = self.check(which={}, recorded=gone)      # a recorded path that is gone counts for nothing
        self.assertEqual(len(problems), 2)

    def test_recorded_py_gh_runs_through_this_python(self):  # T3 -> AC3 (D5)
        calls = []
        fake = self.root / "gh.py"
        fake.write_text("x")
        self.check(which={"git": "/usr/bin/git"}, recorded={"gh": fake.as_posix()},
                   run=fake_run(calls=calls))
        self.assertTrue(calls and all(c[:2] == [sys.executable, fake.as_posix()] for c in calls))

    def test_commit_info_uses_the_recorded_git(self):  # T3 -> AC3
        clone = self.root / "clone"
        git = lambda *a: subprocess.run(["git", "-C", str(clone), *a], check=True,  # noqa: E731
                                        capture_output=True, text=True).stdout.strip()
        subprocess.run(["git", "init", "-q", str(clone)], check=True)
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "t")
        git("config", "commit.gpgsign", "false")
        (clone / "a.txt").write_text("a")
        git("add", ".")
        git("commit", "-q", "-m", "c")
        real = shutil.which("git")
        self.assertEqual(install.commit_info(clone, None, real)["commit"], git("rev-parse", "HEAD"))
        self.assertEqual(install.commit_info(clone, None, str(self.root / "no-such-git")), {})
        # `git` found only through the config: install() still records commit and commit_time
        (self.home / ".claude" / "agent-vault.json").write_text(json.dumps({"git": real}))
        self.install_with(which={"gh": "/usr/bin/gh"}, repo=clone)
        cfg = json.loads((self.home / ".claude" / "agent-vault.json").read_text())
        self.assertEqual(cfg["commit"], git("rev-parse", "HEAD"))
        self.assertIsInstance(cfg["commit_time"], int)
        self.assertEqual(cfg["git"], Path(real).as_posix())

    def test_token_never_printed(self):  # T1 -> AC1 (D1)
        msg = self.refuse(run=fake_run(token_rc=1))
        self.assertNotIn(SECRET, msg)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            report = self.install_with()
        self.assertNotIn(SECRET, out.getvalue() + "\n".join(report))

    def test_subprocess_with_selfupdate_flags_still_refuses(self):  # T1 -> AC1
        empty = self.root / "empty-path"
        empty.mkdir()
        env = {k: v for k, v in os.environ.items() if k != install.TEST_PREREQS_ENV}
        env["PATH"] = str(empty)
        before = self.listing()
        out = subprocess.run([sys.executable, str(REPO / "install.py"), "--vault", str(self.vault),
                              "--home", str(self.home), "--repo", str(self.root / "repo"),
                              "--commit", "0" * 40], capture_output=True, text=True, env=env)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("https://cli.github.com", out.stderr)
        self.assertIn("install git", out.stderr)
        self.assertEqual(self.listing(), before)


class PrerequisiteDocsTest(unittest.TestCase):  # T5 -> AC5
    def test_readme_names_the_prerequisites_and_recorded_paths(self):
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        for phrase in ("Python 3.9+", "gh 2.94.0", "gh auth login", "paths of the `gh` and `git`"):
            self.assertIn(phrase, readme)

    def test_claude_md_names_the_lookup_order_and_the_check(self):
        text = (REPO / "CLAUDE.md").read_text(encoding="utf-8")
        for phrase in ("`AGENT_VAULT_GH`, else the config's `gh`, else PATH", "the config's `git`, else PATH",
                       "checks Python 3.9+", "records the absolute paths of `gh` and `git`"):
            self.assertIn(phrase, text)


class OptOutCoverageTest(unittest.TestCase):  # R1 of #47: no test may run the real prerequisite check by accident
    def test_every_test_that_installs_opts_out(self):
        import ast
        missing = []
        for path in sorted((REPO / "tests").glob("test_*.py")):
            text = path.read_text(encoding="utf-8")
            for node in ast.walk(ast.parse(text)):
                if not isinstance(node, ast.ClassDef) or node.name == "OptOutCoverageTest":
                    continue
                body = ast.get_source_segment(text, node) or ""
                runs_installer = "install.install(" in body or 'str(REPO / "install.py")' in body
                if runs_installer and not ("skip_install_prereqs" in body or "TEST_PREREQS_ENV" in body):
                    missing.append(f"{path.name}::{node.name}")
        self.assertEqual(missing, [])
