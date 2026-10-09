import subprocess
import unittest
from pathlib import Path

from tests.helpers import CODE, make_vault, run_py, tmpdir, write_config

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
        for cmd in [f"cd {self.repo} && git push", "cd r && git push origin 2>&1 | tail -5",
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
        import importlib.util
        import os
        spec = importlib.util.spec_from_file_location("pg", GUARD)
        pg = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pg)
        os.environ["AV_TEST_DIR"] = "/x/y"
        self.assertEqual(pg.resolve_dir("/a", "$AV_TEST_DIR/z"), os.path.normpath("/x/y/z"))
        if os.name == "nt":
            self.assertEqual(pg.resolve_dir("C:/a", "/c/Users/me"), os.path.normpath("C:/Users/me"))
            os.environ["AV_TEST_DIR"] = "/c/Users"
            self.assertEqual(pg.resolve_dir("C:/a", "$AV_TEST_DIR/me"), os.path.normpath("C:/Users/me"))

    def test_ignores_other_tools(self):
        r = run_py(GUARD, stdin={"tool_name": "Write", "tool_input": {"file_path": "x"}})
        self.assertEqual(r.returncode, 0)


class IssueGuardTest(unittest.TestCase):  # T5, T6 -> AC7, AC8
    """`gh issue create` against a registered repo; needs a temp vault config."""

    def setUp(self):
        self._t = tmpdir()
        self.root = Path(self._t.name)
        vault = make_vault(self.root)
        projects = vault / "Agent" / "Projects"
        projects.mkdir(parents=True)
        (projects / "widgets.md").write_text(
            "---\ntype: project\nid: widgets\nstatus: active\nrepos: [acme/widget]\n---\n# widgets\n",
            encoding="utf-8")
        self.cfg = write_config(self.root, vault)
        self.clone = self.make_clone("clone", "https://github.com/acme/widget.git")
        self.other = self.make_clone("other", "https://github.com/acme/unlisted.git")
        self.elsewhere = self.root / "elsewhere"
        self.elsewhere.mkdir()

    def tearDown(self):
        self._t.cleanup()

    def make_clone(self, name, url):
        path = self.root / name
        path.mkdir()
        git(path, "init", "-q", "-b", "feat/1-x")
        git(path, "remote", "add", "origin", url)
        return path

    def run_guard(self, command, cwd, tool="Bash", config="default"):
        cfg = self.cfg if config == "default" else config
        return run_py(GUARD, stdin={"tool_name": tool, "cwd": str(cwd), "tool_input": {"command": command}},
                      config=cfg)

    def code(self, command, cwd=None, **kw):
        return self.run_guard(command, cwd or self.clone, **kw).returncode

    def test_blocks(self):
        c, e = self.clone, self.elsewhere
        for cmd, cwd in [
            ("gh issue create --title x --body y", c),
            ("gh issue new --title x", c),
            ("gh.exe issue create --title x", c),
            (f"cd {c} && gh issue create --title x", e),
            ("gh issue create -R acme/widget --title x", e),
            ("gh issue create --repo=acme/widget --title x", e),
            ("gh issue create --repo https://github.com/acme/widget --title x", e),
            ("gh issue -R acme/widget create --title x", e),
            ("GH_REPO=acme/widget gh issue create --title x", e),
            ("bash -c 'gh issue create --title x'", c),
        ]:
            with self.subTest(cmd=cmd):
                r = self.run_guard(cmd, cwd)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertIn("vault.py", r.stderr)
                self.assertIn("issue create", r.stderr)
                self.assertIn("--standalone", r.stderr)
                self.assertIn("--bug", r.stderr)

    def test_allows(self):
        c, o, e = self.clone, self.other, self.elsewhere
        for cmd, cwd in [
            ("gh issue create --title x", o),
            ("gh issue create -R other/repo --title x", c),
            ("GH_REPO=other/repo gh issue create --title x", c),
            ("gh issue list", c),
            ("gh issue view 3", c),
            ("gh issue edit 3 --add-label x", c),
            ("gh pr create --title x", c),
            ("echo gh issue create", c),
            ("cat <<'EOF'\ngh issue create --title x\nEOF", c),
            ("python /x/vault.py issue create widget --title x --body-file b.md", c),
            ("gh issue create --title x", e),
            ("gh issue create --title x", self.root / "no-such-dir"),
        ]:
            with self.subTest(cmd=cmd):
                self.assertEqual(self.code(cmd, cwd), 0)

    def test_blocks_removing_a_features_label_or_links(self):     # T13 -> AC13
        c, e = self.clone, self.elsewhere
        graphql = "gh api graphql -f query='mutation{removeSubIssue(input:{issueId:\"a\",subIssueId:\"b\"}){issue{id}}}'"
        for cmd, cwd in [
            ("gh issue edit 5 --remove-parent", c),
            ("gh issue edit 5 --remove-sub-issue 7", c),
            ("gh issue edit 5 --remove-label bug,feature", c),
            ("gh issue edit 5 --remove-label=Feature", c),
            ("gh -R acme/widget issue edit 5 --remove-label feature", e),
            ("gh label delete feature", c),
            ("gh label delete feature --yes -R acme/widget", e),
            (graphql, c),
            ("gh api -X DELETE repos/acme/widget/issues/5/sub_issue -F sub_issue_id=7", c),
            ("gh api --method DELETE repos/acme/widget/issues/5/labels/feature", e),
            ("gh api --method=DELETE repos/acme/widget/labels/feature", e),
            ("bash -c 'gh issue edit 5 --remove-parent'", c),
            ("gh api -X DELETE repos/{owner}/{repo}/issues/5/sub_issue", c),
            ("gh api --method DELETE repos/{owner}/{repo}/issues/5/labels/feature", c),
        ]:
            with self.subTest(cmd=cmd):
                r = self.run_guard(cmd, cwd)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertIn("user", r.stderr)
                self.assertIn("by hand", r.stderr)

    def test_allows_other_edits_and_other_repos(self):            # T13 -> AC13
        c, o = self.clone, self.other
        for cmd, cwd in [
            ("gh issue edit 5 --remove-label bug", c),
            ("gh issue edit 5 --add-sub-issue 7", c),
            ("gh issue edit 5 --add-label planned", c),
            ("gh label delete bug", c),
            ("gh api repos/acme/widget/issues/5/sub_issues", c),
            ("gh api -X DELETE repos/acme/widget/issues/5/labels/bug", c),
            ("gh issue edit 5 --remove-parent", o),
            ("gh issue edit 5 --remove-label feature -R other/repo", c),
            ("gh label delete feature -R other/repo", c),
            ("gh api -X DELETE repos/other/repo/issues/5/sub_issue", c),
        ]:
            with self.subTest(cmd=cmd):
                self.assertEqual(self.code(cmd, cwd), 0)

    def test_option_values_are_not_read_as_repo(self):
        r = self.run_guard('gh issue create --title "-Rx" --body y', self.clone)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_gh_repo_must_precede_gh(self):
        r = self.run_guard("gh issue create --title x --body GH_REPO=other/repo", self.clone)
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_allows_when_the_config_is_unusable(self):
        cmd = "gh issue create --title x"
        self.assertEqual(self.code(cmd, config=self.root / "absent.json"), 0)
        nowhere = self.root / "nowhere.json"
        nowhere.write_text('{"vault": "' + (self.root / "missing").as_posix() + '"}', encoding="utf-8")
        self.assertEqual(self.code(cmd, config=nowhere), 0)
        bad = self.root / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        self.assertEqual(self.code(cmd, config=bad), 0)

    def test_powershell(self):
        c, e = self.clone, self.elsewhere
        self.assertEqual(self.code("gh issue create --title x", c, tool="PowerShell"), 2)
        self.assertEqual(self.code("gh issue list", c, tool="PowerShell"), 0)
        for cmd in [f"Set-Location {c}; gh issue create --title x", f"Push-Location {c}; gh issue create --title x",
                    f"sl {c}; gh issue create --title x", f"Set-Location -Path {c}; gh issue create --title x",
                    f"set-location -LiteralPath {c}; gh issue create --title x"]:
            with self.subTest(cmd=cmd):
                self.assertEqual(self.code(cmd, e, tool="PowerShell"), 2)
        self.assertEqual(self.code("git push origin main", c, tool="PowerShell"), 2)
        git(c, "checkout", "-q", "-B", "main")
        git(c, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x")
        self.assertEqual(self.code(f"sl {c}; git push", e, tool="PowerShell"), 2)
        self.assertEqual(self.code("gh issue create --title x", c, tool="Write"), 0)


if __name__ == "__main__":
    unittest.main()
