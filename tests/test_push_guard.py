import subprocess
import unittest
from pathlib import Path

from tests.helpers import CODE, run_py, tmpdir

GUARD = CODE / "hooks" / "push_guard.py"


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


class PushGuardTest(unittest.TestCase):  # T5 -> AC6
    def setUp(self):
        self._t = tmpdir()
        self.repo = Path(self._t.name) / "r"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x")

    def tearDown(self):
        self._t.cleanup()

    def check(self, command, branch="feat/1-x", cwd=None):
        git(self.repo, "checkout", "-q", "-B", branch)
        r = run_py(GUARD, stdin={"tool_name": "Bash", "cwd": str(cwd or self.repo),
                                 "tool_input": {"command": command}})
        return r.returncode

    def test_blocks_from_elsewhere(self):
        elsewhere = Path(self._t.name)
        for cmd in [f"cd {self.repo} && git push", f"cd r && git push origin 2>&1 | tail -5",
                    f"pushd {self.repo}; git push"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(self.check(cmd, "main", cwd=elsewhere), 2)
        self.assertEqual(self.check(f"cd {self.repo} && git push", "feat/1-x", cwd=elsewhere), 0)

    def test_blocks_windows_paths(self):  # AC11
        elsewhere = Path(self._t.name)
        for cmd in [f"git -C {self.repo} push", f'git -C "{self.repo}" push origin',
                    f"cd {self.repo} && git push", f"pushd {self.repo}; git push"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(self.check(cmd, "main", cwd=elsewhere), 2)

    def test_blocks(self):
        for cmd, branch in [
            ("git push origin main", "feat/1-x"),
            ("git push origin HEAD:main", "feat/1-x"),
            ("git push -f origin +main", "feat/1-x"),
            ("git push origin feat/1-x:refs/heads/main", "feat/1-x"),
            ("git push", "main"),
            ("git push -u origin HEAD", "main"),
            ("git push --all origin", "feat/1-x"),
            ("git push --no-verify origin feat/1-x", "feat/1-x"),
            ("git commit --no-verify -m x", "feat/1-x"),
            ("git commit -nm x", "feat/1-x"),
            ("git add . && git commit -m x && git push origin main", "feat/1-x"),
            (f"git -C {self.repo} push", "main"),
            ("git push origin 2>&1", "main"),
            ("git push origin >/tmp/x", "main"),
            ("git push origin 2>&1 | tail -5", "main"),
            ("bash -c 'git push origin main'", "feat/1-x"),
            ("(git push origin main)", "feat/1-x"),
            ("timeout 60 git push origin main", "feat/1-x"),
            ("env X=1 git push origin main", "feat/1-x"),
            ("/usr/bin/git push origin main", "feat/1-x"),
            ("git push \\\n  origin main", "feat/1-x"),
            ("git -c core.hooksPath=/dev/null push origin feat/1-x", "feat/1-x"),
            ('git commit -m "a && b" --no-verify', "feat/1-x"),
            ("git push # push it", "main"),
            ("if true; then git push origin main; fi", "feat/1-x"),
            ("for b in x; do git push origin main; done", "feat/1-x"),
            ("env -u FOO git push origin main", "feat/1-x"),
            ("sudo git push origin main", "feat/1-x"),
            ("bash -lc 'git push origin main'", "feat/1-x"),
            ("(cd /tmp) && git push", "main"),
            ("cat <<\\EOF\nit's\nEOF\ngit push origin main", "feat/1-x"),
            ("git push --branches origin", "feat/1-x"),
            ("git config core.hooksPath /dev/null", "feat/1-x"),
            ("git config --unset core.hooksPath", "feat/1-x"),
            ('git commit -m "#12 fix" --no-verify', "feat/1-x"),
            ("git commit -m \"$(cat <<'EOF'\nfeat: x\n\nIt's done; a && b | c\nEOF\n)\" && git push origin main", "feat/1-x"),
            ("git commit -m \"$(cat <<'EOF'\nmsg; more\nEOF\n)\" --no-verify", "feat/1-x"),
        ]:
            with self.subTest(cmd=cmd, branch=branch):
                self.assertEqual(self.check(cmd, branch), 2)

    def test_allows(self):
        for cmd, branch in [
            ("git push origin feat/1-x", "feat/1-x"),
            ("git push -u origin feat/1-x", "feat/1-x"),
            ("git push", "feat/1-x"),
            ("git push origin v1.0.0", "main"),
            ("git commit -m 'fix main page'", "main"),
            ("git log main", "main"),
            ("echo git push origin main is blocked", "feat/1-x"),
            ("ls -la", "main"),
            ("gh pr create --body \"$(cat <<'EOF'\ngit push origin main is now blocked\ngit commit --no-verify too\nEOF\n)\"", "feat/1-x"),
            ("cat > notes.txt <<'EOF'\ngit push origin main\nEOF", "feat/1-x"),
            ("git push --tags", "main"),
            ("git push origin --tags", "main"),
            ("git commit -uno -m x", "feat/1-x"),
            ('git commit -m"note"', "feat/1-x"),
            ("git push origin feat/1-x 2>&1 | tail -3", "feat/1-x"),
            ("git config --get core.hooksPath", "feat/1-x"),
            ("git config core.hooksPath", "feat/1-x"),
            ("git commit -m 'refs issue #12'", "main"),
        ]:
            with self.subTest(cmd=cmd, branch=branch):
                self.assertEqual(self.check(cmd, branch), 0)

    def test_git_bash_paths(self):
        import importlib.util, os
        spec = importlib.util.spec_from_file_location("pg", GUARD)
        pg = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pg)
        os.environ["AV_TEST_DIR"] = "/x/y"
        self.assertEqual(pg.resolve_dir("/a", "$AV_TEST_DIR/z"), os.path.normpath("/x/y/z"))
        if os.name == "nt":
            self.assertEqual(pg.resolve_dir("C:/a", "/c/Users/me"), os.path.normpath("C:/Users/me"))

    def test_ignores_other_tools(self):
        r = run_py(GUARD, stdin={"tool_name": "Write", "tool_input": {"file_path": "x"}})
        self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
