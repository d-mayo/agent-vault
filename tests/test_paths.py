import posixpath
import sys
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import CODE, make_vault, run_py, tmpdir, write_config

sys.path.insert(0, str(CODE))
import lib  # noqa: E402

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
        r = run_py(HOOKS / "session_start.py", stdin={"session_id": "t", "cwd": str(self.vault)}, config=self.cfg)
        self.assertEqual(r.returncode, 0)
        self.assertIn("Vault check: OK", r.stdout)
        self.assertTrue(list((self.vault / "Agent" / "Daily").glob("*.md")))

    def test_phone_log_location(self):
        (self.vault / "Agent").mkdir()
        (self.vault / "Agent" / ".phone-log.json").write_text('{"Ideas.md": "2026-09-26"}\n')
        (self.vault / "Ideas.md").write_text("an idea\n")
        r = run_py(CLI, ["phone", "list"], config=self.cfg)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("no phone-side notes waiting", r.stdout)
        r = run_py(HOOKS / "guard.py", config=self.cfg, stdin={
            "tool_name": "Edit", "cwd": str(self.vault), "tool_input": {"file_path": "Ideas.md"}})
        self.assertEqual(r.returncode, 2)

    def test_old_handoff_log_is_not_read(self):
        (self.vault / "Agent").mkdir()
        (self.vault / "Agent" / ".handoff-log.json").write_text('{"Ideas.md": "2026-09-26"}\n')
        (self.vault / "Ideas.md").write_text("an idea\n")
        r = run_py(CLI, ["phone", "list"], config=self.cfg)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Ideas.md", r.stdout)


class PathHelpersTest(unittest.TestCase):
    """norm_path and parts_under follow the platform rule (D1 of #43); posix paths and a patched
    sys.platform make this hold on every machine."""

    def on(self, platform):
        p = mock.patch.object(sys, "platform", platform)
        q = mock.patch.object(lib.os, "path", posixpath)
        p.start(), q.start()
        self.addCleanup(p.stop)
        self.addCleanup(q.stop)

    def test_darwin_folds_case(self):
        self.on("darwin")
        self.assertEqual(lib.norm_path("/Vault/Notes/A.md"), lib.norm_path("/vault/NOTES/a.md"))
        self.assertEqual(lib.parts_under("/VAULT/notes/Agent/Foo.md", "/Vault/Notes"), ["Agent", "Foo.md"])

    def test_linux_does_not_fold_case(self):
        self.on("linux")
        self.assertNotEqual(lib.norm_path("/Vault/A.md"), lib.norm_path("/vault/a.md"))
        self.assertIsNone(lib.parts_under("/VAULT/notes/Agent", "/Vault/Notes"))
        self.assertEqual(lib.parts_under("/Vault/Notes/Agent", "/Vault/Notes"), ["Agent"])

    def test_parts_under_keeps_the_spelling_below_the_root(self):
        self.on("darwin")
        self.assertEqual(lib.parts_under("/vault/NOTES/Agent/Work/Mixed-Case.md", "/Vault/Notes"),
                         ["Agent", "Work", "Mixed-Case.md"])
        self.assertEqual(lib.parts_under("/vault/notes", "/Vault/Notes"), [])

    def test_parts_under_is_none_outside_the_root(self):
        self.on("darwin")
        self.assertIsNone(lib.parts_under("/vault/Other/x.md", "/Vault/Notes"))
        self.assertIsNone(lib.parts_under("/vault", "/Vault/Notes"))
        self.assertIsNone(lib.parts_under("/vault/Notes2/x.md", "/Vault/Notes"))


if __name__ == "__main__":
    unittest.main()
