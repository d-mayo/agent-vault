import json
import shutil
import sys
import unittest
from pathlib import Path

from tests.helpers import REPO, make_vault, tmpdir

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
        self.assertEqual(cfg["vault"], self.vault.resolve().as_posix())
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
        # v1 migration: code gone, handoff log kept
        self.assertFalse((self.vault / "Agent" / "_system").exists())
        self.assertIn("a.md", (self.vault / "Agent" / ".handoff-log.json").read_text())
        self.assertFalse([p for p in self.vault.rglob("*.py")])

    def test_rerun_identical(self):  # T2 -> AC2
        self.run_install()
        before = snapshot(self.home, self.vault)
        self.run_install()
        self.assertEqual(before, snapshot(self.home, self.vault))
        us = json.loads((self.home / ".claude" / "settings.json").read_text())
        ours = [h for g in us["hooks"]["PreToolUse"] for h in g["hooks"] if "push_guard" in h["command"]]
        self.assertEqual(len(ours), 1)

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

    def test_rejects_non_vault(self):
        with self.assertRaises(SystemExit):
            install.install(self.root / "home", self.home)


if __name__ == "__main__":
    unittest.main()
