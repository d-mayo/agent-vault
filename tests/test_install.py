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
        vs = json.loads((self.vault / ".claude" / "settings.json").read_text())
        cmds = [h["command"] for g in sum(vs["hooks"].values(), []) for h in g["hooks"]]
        self.assertEqual(len(cmds), 4)
        self.assertTrue(all("/.claude/agent-vault/hooks/" in c for c in cmds))
        self.assertFalse(any("Agent/_system" in c for c in cmds))
        self.assertIn("Bash(ls:*)", vs["permissions"]["allow"])
        self.assertFalse(any("Agent/_system" in a for a in vs["permissions"]["allow"]))
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
        self.assertEqual(json.loads(us.read_text())["permissions"]["allow"], [mine])
        vs = json.loads((self.vault / ".claude" / "settings.json").read_text())
        ours = [a for a in vs["permissions"]["allow"] if "/.claude/agent-vault/vault.py" in a]
        self.assertEqual(len(ours), 1)

    def test_no_empty_permissions_added(self):
        self.run_install()
        self.assertNotIn("permissions", json.loads((self.home / ".claude" / "settings.json").read_text()))

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
        self.assertIn(FOREIGN, [h for g in vs["hooks"]["Stop"] for h in g["hooks"]])

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
