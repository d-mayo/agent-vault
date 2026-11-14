"""agent_vault/selfupdate.py: installs what `main` holds, only when it is newer than the
installed commit. The installer it runs is a fake that records its arguments."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import (CODE, FAKE_INSTALLER, make_source_clone, make_vault, tmpdir,
                           write_config, write_source_config)

sys.path.insert(0, str(CODE))
import lib  # noqa: E402
import selfupdate  # noqa: E402


class SelfUpdateCase(unittest.TestCase):
    def setUp(self):
        self._t = tmpdir()
        self.root = Path(self._t.name)
        self.vault = make_vault(self.root)

    def tearDown(self):
        self._t.cleanup()

    def setup_clone(self, installer=FAKE_INSTALLER, commit_time="older", **extra):
        self.clone, self.sha, self.when = make_source_clone(self.root, installer)
        ct = {"older": self.when - 100, "equal": self.when, "newer": self.when + 100,
              "missing": None}[commit_time]
        self.cfg = write_source_config(self.root, self.vault, self.clone, ct, **extra)

    def run_update(self):
        with mock.patch.object(lib, "CONFIG", self.cfg):
            return selfupdate.selfupdate()

    def records(self):
        p = self.root / "record.jsonl"
        return [json.loads(ln) for ln in p.read_text().splitlines()] if p.exists() else []


class DecisionTest(SelfUpdateCase):
    def test_installs_main_not_the_branch(self):  # T2 -> AC2
        self.setup_clone()
        line = self.run_update()
        recs = self.records()
        self.assertEqual(len(recs), 1)
        r = recs[0]
        self.assertEqual(r["argv"][r["argv"].index("--vault") + 1], self.vault.as_posix())
        self.assertEqual(r["argv"][r["argv"].index("--repo") + 1], self.clone.as_posix())
        self.assertEqual(r["argv"][r["argv"].index("--commit") + 1], self.sha)
        self.assertEqual(Path(r["argv"][r["argv"].index("--home") + 1]).resolve(), self.root.resolve())
        self.assertEqual(r["marker"], "main")
        self.assertFalse(r["has_feature_file"])
        self.assertFalse(Path(r["folder"]).exists())
        self.assertIn(self.sha[:7], line)
        self.assertIn("next session", line)
        self.assertFalse(self.cfg.with_name(selfupdate.LOCK_NAME).exists())

    def test_current_install_does_nothing(self):  # T2 -> AC2
        self.setup_clone(commit_time="equal")
        self.assertIsNone(self.run_update())
        self.assertEqual(self.records(), [])

    def test_newer_install_does_nothing(self):  # T2 -> AC2
        self.setup_clone(commit_time="newer")
        self.assertIsNone(self.run_update())
        self.assertEqual(self.records(), [])

    def test_missing_commit_time_installs(self):  # T2 -> AC2
        self.setup_clone(commit_time="missing")
        self.assertIn(self.sha[:7], self.run_update())
        self.assertEqual(len(self.records()), 1)

    def test_config_outside_a_claude_folder(self):  # T2 -> AC2
        self.setup_clone()
        data = json.loads(self.cfg.read_text())
        self.cfg = self.root / "agent-vault.json"
        self.cfg.write_text(json.dumps(data))
        line = self.run_update()
        self.assertTrue(line.startswith("agent-vault:"), line)
        self.assertIn("install.py --vault", line)
        self.assertEqual(self.records(), [])


class LockTest(SelfUpdateCase):
    def test_fresh_lock_blocks(self):  # T3 -> AC3
        self.setup_clone()
        lock = self.cfg.with_name(selfupdate.LOCK_NAME)
        lock.write_text("")
        self.assertIsNone(self.run_update())
        self.assertEqual(self.records(), [])
        self.assertTrue(lock.exists())

    def test_stale_lock_is_replaced(self):  # T3 -> AC3
        self.setup_clone()
        lock = self.cfg.with_name(selfupdate.LOCK_NAME)
        lock.write_text("")
        old = time.time() - 300
        os.utime(lock, (old, old))
        self.assertIn(self.sha[:7], self.run_update())
        self.assertFalse(lock.exists())

    def test_lock_removed_after_a_failure(self):  # T3 -> AC3
        self.setup_clone(installer="import sys; sys.exit(1)\n")
        self.assertTrue(self.run_update().startswith("agent-vault:"))
        self.assertFalse(self.cfg.with_name(selfupdate.LOCK_NAME).exists())


class FailureTest(SelfUpdateCase):
    def assert_by_hand(self, line, *cause):
        self.assertIsNotNone(line)
        self.assertTrue(line.startswith("agent-vault:"), line)
        self.assertIn("install.py --vault", line)
        for c in cause:
            self.assertIn(c, line)
        self.assertEqual(len(line.splitlines()), 1)

    def test_no_python(self):  # T4 -> AC4
        self.setup_clone()
        data = json.loads(self.cfg.read_text())
        del data["python"]
        self.cfg.write_text(json.dumps(data))
        self.assert_by_hand(self.run_update(), "python")

    def test_source_missing(self):  # T4 -> AC4
        self.setup_clone()
        write_source_config(self.root, self.vault, self.root / "nope", 0)
        self.assert_by_hand(self.run_update(), "missing")

    def test_source_is_a_plain_folder(self):  # T4 -> AC4
        self.setup_clone()
        plain = self.root / "plain"
        plain.mkdir()
        write_source_config(self.root, self.vault, plain, 0)
        self.assert_by_hand(self.run_update(), "main")

    def test_clone_without_main(self):  # T4 -> AC4
        self.setup_clone()
        subprocess.run(["git", "-C", str(self.clone), "branch", "-m", "main", "trunk"], check=True)
        self.assert_by_hand(self.run_update(), "main")

    def test_installer_exits_nonzero(self):  # T4 -> AC4
        self.setup_clone(installer="import sys\nprint('first', file=sys.stderr)\n"
                                   "print('error: it broke', file=sys.stderr)\nsys.exit(1)\n")
        self.assert_by_hand(self.run_update(), "it broke")

    def test_installer_too_slow(self):  # T4 -> AC4
        self.setup_clone(installer="import time\ntime.sleep(30)\n")
        with mock.patch.object(selfupdate, "INSTALL_LIMIT", 1):
            line = self.run_update()
        self.assert_by_hand(line, "longer than")
        self.assertFalse(self.cfg.with_name(selfupdate.LOCK_NAME).exists())

    def test_nothing_to_say(self):  # T4 -> AC2
        self.setup_clone()
        cfg = write_config(self.root, self.vault)          # no source, no .claude parent
        bad = self.root / "bad.json"
        bad.write_text("{not json")
        for path in (cfg, self.root / "absent.json", bad):
            self.cfg = path
            self.assertIsNone(self.run_update())
        self.assertEqual(self.records(), [])


if __name__ == "__main__":
    unittest.main()
