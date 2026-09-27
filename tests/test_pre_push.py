import shutil
import subprocess
import unittest

from tests.helpers import REPO

HOOK = REPO / "agent_vault" / "githooks" / "pre-push"
Z = "0" * 40


@unittest.skipUnless(shutil.which("bash"), "bash not available")
class PrePushTest(unittest.TestCase):  # T4 -> AC5
    def run_hook(self, line):
        return subprocess.run(["bash", str(HOOK), "origin", "url"], input=line + "\n",
                              text=True, capture_output=True).returncode

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
                self.assertEqual(self.run_hook(line), want)


if __name__ == "__main__":
    unittest.main()
