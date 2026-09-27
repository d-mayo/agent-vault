import unittest
from pathlib import Path

from tests.helpers import CODE, make_vault, run_py, tmpdir, write_config

CLI = CODE / "vault.py"
HOOKS = CODE / "hooks"


class PathsTest(unittest.TestCase):
    def setUp(self):
        self._t = tmpdir()
        self.root = Path(self._t.name)
        self.vault = make_vault(self.root)
        self.cfg = write_config(self.root, self.vault)

    def tearDown(self):
        self._t.cleanup()

    def test_missing_config(self):
        missing = self.root / "nope.json"
        r = run_py(CLI, ["status"], config=missing)
        self.assertEqual(r.returncode, 1)
        self.assertIn("run install.py", r.stderr)
        for hook in ["session_start.py", "guard.py", "post_edit.py", "stop_check.py"]:
            with self.subTest(hook=hook):
                r = run_py(HOOKS / hook, stdin={"session_id": "t"}, config=missing)
                self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))

    def test_stale_config_is_unconfigured(self):
        gone = self.root / "Moved"
        cfg = write_config(self.root, gone)
        r = run_py(HOOKS / "session_start.py", stdin={"session_id": "t"}, config=cfg)
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertFalse(gone.exists())

    def test_config_override(self):
        r = run_py(HOOKS / "session_start.py", stdin={"session_id": "t"}, config=self.cfg)
        self.assertEqual(r.returncode, 0)
        self.assertIn("Vault check: OK", r.stdout)
        self.assertTrue(list((self.vault / "Agent" / "Daily").glob("*.md")))

    def test_handoff_log_location(self):
        (self.vault / "Agent").mkdir()
        (self.vault / "Agent" / ".handoff-log.json").write_text('{"Ideas.md": "2026-09-26"}\n')
        (self.vault / "Ideas.md").write_text("an idea\n")
        r = run_py(CLI, ["handoff", "list"], config=self.cfg)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("no phone-side notes waiting", r.stdout)
        r = run_py(HOOKS / "guard.py", config=self.cfg, stdin={
            "tool_name": "Edit", "cwd": str(self.vault), "tool_input": {"file_path": "Ideas.md"}})
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
