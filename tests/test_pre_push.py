import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from tests.helpers import REPO, tmpdir

HOOK = REPO / "agent_vault" / "githooks" / "pre-push"
Z = "0" * 40
PY = Path(sys.executable).as_posix()


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@unittest.skipUnless(shutil.which("bash"), "bash not available")
class PrePushTest(unittest.TestCase):  # T4 -> AC5; T7, T8 -> AC7, AC8 (#33)
    def setUp(self):
        self._t = tmpdir()
        self.addCleanup(self._t.cleanup)
        self.root = Path(self._t.name)
        self.clone = self.make_clone("git --version")
        self.cfg = self.write_config({"vault": (self.root / "Notes").as_posix(), "python": PY,
                                      "managed_allow": []})

    def make_clone(self, lint, name="clone"):
        repo = self.root / name
        repo.mkdir()
        git("init", "-q", "-b", "main", cwd=repo)
        (repo / "CLAUDE.md").write_text(
            f"# demo\n\n## Purpose\nx.\n\n## Commands\n- Lint: `{lint}`\n", encoding="utf-8", newline="\n")
        return repo

    def write_config(self, data):
        cfg = self.root / "agent-vault.json"
        cfg.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")      # the installer's layout
        return cfg

    def run_hook(self, line, clone=None, config="default"):
        env = {**os.environ}
        env.pop("AGENT_VAULT_CONFIG", None)
        cfg = self.cfg if config == "default" else config
        env["AGENT_VAULT_CONFIG"] = str(cfg if cfg is not None else self.root / "none.json")
        return subprocess.run(["bash", HOOK.as_posix(), "origin", "url"], input=line + "\n", text=True,
                              capture_output=True, cwd=clone or self.clone, env=env)

    def test_cases(self):
        cases = {
            f"refs/heads/x abc refs/heads/main {Z}": 1,
            f"refs/heads/x abc refs/heads/my-branch {Z}": 1,
            f"refs/heads/x abc refs/heads/feat/12 {Z}": 1,
            f"refs/heads/x abc refs/heads/feat/12-note-schema {Z}": 0,
            f"refs/heads/x abc refs/heads/docs/1-design-spec {Z}": 0,
            f"refs/tags/v1.0.0 abc refs/tags/v1.0.0 {Z}": 0,
            f"(delete) {Z} refs/heads/old-branch abc": 0,
            f"(delete) {'0' * 64} refs/heads/old-branch abc": 0,
            f"refs/notes/commits abc refs/notes/commits {Z}": 0,
        }
        for line, want in cases.items():
            with self.subTest(line=line):
                self.assertEqual(self.run_hook(line).returncode, want)

    def test_failing_lint_refuses_a_valid_branch(self):
        bad = self.make_clone("git not-a-subcommand", "bad")
        r = self.run_hook(f"refs/heads/x abc refs/heads/feat/12-note-schema {Z}", clone=bad)
        self.assertEqual(r.returncode, 1)
        self.assertIn("lint failed", r.stderr)
        self.assertIn("git not-a-subcommand", r.stderr)

    def test_deletions_and_tags_pass_without_linting(self):
        bad = self.make_clone("git not-a-subcommand", "bad")
        for line in (f"(delete) {Z} refs/heads/old-branch abc", f"refs/tags/v1 abc refs/tags/v1 {Z}"):
            with self.subTest(line=line):
                self.assertEqual(self.run_hook(line, clone=bad, config=None).returncode, 0)

    def test_main_and_bad_names_are_refused_before_lint(self):
        bad = self.make_clone("git not-a-subcommand", "bad")
        r = self.run_hook(f"refs/heads/x abc refs/heads/main {Z}", clone=bad)
        self.assertEqual(r.returncode, 1)
        self.assertIn("main", r.stderr)

    def refused_with_install_hint(self, **kw):
        r = self.run_hook(f"refs/heads/x abc refs/heads/feat/12-note-schema {Z}", **kw)
        self.assertEqual(r.returncode, 1)
        self.assertIn("install.py", r.stderr)

    def test_no_config_refuses(self):
        self.refused_with_install_hint(config=None)

    def test_config_without_python_refuses(self):
        self.refused_with_install_hint(config=self.write_config({"vault": "x", "managed_allow": []}))

    def test_python_naming_a_missing_file_refuses(self):
        self.refused_with_install_hint(config=self.write_config(
            {"vault": "x", "python": (self.root / "no-python.exe").as_posix(), "managed_allow": []}))

    def test_hook_has_no_carriage_returns_and_keeps_its_marker(self):
        raw = HOOK.read_bytes()
        self.assertNotIn(b"\r", raw)
        self.assertIn("agent-vault-hook: pre-push", raw.decode("utf-8").splitlines()[1])


if __name__ == "__main__":
    unittest.main()
