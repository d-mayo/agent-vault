"""`claudemd.py`: parses and lints a repo's CLAUDE.md (design-v1.md §8). Real git runs
against a temp repo; nothing here touches GitHub."""
from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import CODE, tmpdir

sys.path.insert(0, str(CODE))
import claudemd  # noqa: E402

CLEAN = """# demo

## Purpose
A demo repo.

## Commands
<!-- covers: install.py -->
- Run tests: `python -m unittest discover`

## Layout
<!-- covers: src/** -->
- `src/`: the code.

## Conventions
- Nothing special.

## Gotchas
- None yet.
"""


def git(*args, cwd, check=True):
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True).stdout.strip()


class ClaudemdCase(unittest.TestCase):
    def setUp(self):
        self._t = tmpdir()
        self.addCleanup(self._t.cleanup)
        self.repo = Path(self._t.name) / "repo"
        self.repo.mkdir()
        empty = Path(self._t.name) / "empty-gitconfig"
        empty.write_text("", encoding="utf-8")
        patch = mock.patch.dict(os.environ, {
            "GIT_CONFIG_GLOBAL": str(empty), "GIT_CONFIG_NOSYSTEM": "1"})
        patch.start()
        self.addCleanup(patch.stop)
        git("init", "-q", "-b", "main", cwd=self.repo)
        for k, v in (("user.name", "t"), ("user.email", "t@example.com"), ("commit.gpgsign", "false")):
            git("config", k, v, cwd=self.repo)

    def write(self, rel: str, text: str) -> Path:
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
        return p

    def commit(self, msg: str, date: str = None) -> str:
        git("add", "-A", cwd=self.repo)
        env = dict(os.environ)
        if date:
            ts = f"{date}T12:00:00"
            env["GIT_AUTHOR_DATE"] = ts
            env["GIT_COMMITTER_DATE"] = ts
        subprocess.run(["git", "commit", "-q", "-m", msg], cwd=self.repo, check=True,
                       capture_output=True, text=True, env=env)
        return git("rev-parse", "HEAD", cwd=self.repo)

    def claude(self, text: str) -> Path:
        return self.write("CLAUDE.md", text)

    def lint(self):
        return claudemd.lint(self.repo)


class CleanFileTest(ClaudemdCase):          # T1 -> AC1
    def test_clean_file_has_no_errors(self):
        self.write("install.py", "x\n")
        self.write("src/a.py", "x\n")
        self.claude(CLEAN)
        self.commit("init")
        r = self.lint()
        self.assertEqual(r.errors, [])

    def test_missing_claude_md_is_an_error(self):
        self.write("README.md", "x\n")
        self.commit("init")
        r = self.lint()
        self.assertTrue(any("CLAUDE.md" in e and "does not exist" in e for e in r.errors))


class PathCheckTest(ClaudemdCase):          # T1, T2 -> AC1, AC2
    def test_broken_path_reported(self):
        self.claude("# demo\n\n## Layout\n- `src/missing.py`: nope.\n")
        self.commit("init")
        r = self.lint()
        self.assertTrue(any("src/missing.py" in e and "does not exist" in e for e in r.errors))

    def test_existing_path_passes(self):
        self.write("src/a.py", "x\n")
        self.claude("# demo\n\n## Layout\n- `src/a.py`: the code.\n")
        self.commit("init")
        self.assertEqual(self.lint().errors, [])

    def test_ac2_exclusions_are_not_checked_as_paths(self):     # T2 -> AC2
        self.claude(
            "# demo\n\n## Layout\n"
            "- `~/.claude/agent-vault/`: nope, not local.\n"
            "- `C:/nowhere/here.py`: nope, absolute.\n"
            "- `/etc/nowhere.conf`: nope, root.\n"
            "- `<path to something>`: a placeholder.\n"
            "- `src/**/*.py`: a glob.\n"
        )
        self.commit("init")
        self.assertEqual(self.lint().errors, [])

    def test_commands_section_is_never_path_checked(self):     # D4
        self.claude("# demo\n\n## Commands\n- Run: `bin/does-not-exist --flag`\n")
        self.commit("init")
        errors = self.lint().errors
        self.assertFalse(any("bin/does-not-exist" in e and "path" in e for e in errors))


class CommandCheckTest(ClaudemdCase):        # T1 -> AC1
    def test_executable_on_path_resolves(self):
        bin_dir = Path(self._t.name) / "bin"
        bin_dir.mkdir()
        (bin_dir / "mytool.exe").write_text("", encoding="utf-8")
        self.claude("# demo\n\n## Commands\n- Run: `mytool --flag`\n")
        self.commit("init")
        with mock.patch.dict(os.environ, {"PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", "")}):
            self.assertEqual(self.lint().errors, [])

    def test_unresolvable_command_is_an_error(self):
        self.claude("# demo\n\n## Commands\n- Run: `not-a-real-command-xyz --flag`\n")
        self.commit("init")
        errors = self.lint().errors
        self.assertTrue(any("not-a-real-command-xyz" in e for e in errors))

    def test_relative_script_path_resolves_if_it_exists(self):
        self.write("scripts/run.sh", "#!/bin/sh\n")
        self.claude("# demo\n\n## Commands\n- Run: `scripts/run.sh --flag`\n")
        self.commit("init")
        self.assertEqual(self.lint().errors, [])
        self.claude("# demo\n\n## Commands\n- Run: `scripts/gone.sh --flag`\n")
        self.assertTrue(self.lint().errors)

    def test_npm_script(self):
        self.write("package.json", '{"scripts": {"build": "x"}}')
        self.claude("# demo\n\n## Commands\n- Build: `npm run build`\n")
        self.commit("init")
        self.assertEqual(self.lint().errors, [])
        self.claude("# demo\n\n## Commands\n- Build: `npm run missing`\n")
        self.assertTrue(self.lint().errors)

    def test_make_target(self):
        self.write("Makefile", "lint:\n\techo lint\n")
        self.claude("# demo\n\n## Commands\n- Lint: `make lint`\n")
        self.commit("init")
        self.assertEqual(self.lint().errors, [])
        self.claude("# demo\n\n## Commands\n- Lint: `make missing`\n")
        self.assertTrue(self.lint().errors)

    def test_just_target(self):
        self.write("justfile", "build:\n\techo build\n")
        self.claude("# demo\n\n## Commands\n- Build: `just build`\n")
        self.commit("init")
        self.assertEqual(self.lint().errors, [])
        self.claude("# demo\n\n## Commands\n- Build: `just missing`\n")
        self.assertTrue(self.lint().errors)

    def test_placeholder_first_word_is_skipped(self):
        self.claude("# demo\n\n## Commands\n- Run: `<your tool> --flag`\n")
        self.commit("init")
        self.assertEqual(self.lint().errors, [])


class GlobCheckTest(ClaudemdCase):           # T1 -> AC1
    def test_glob_matching_nothing_is_an_error(self):
        self.claude("# demo\n\n## Layout\n<!-- covers: src/** -->\n- nothing here yet.\n")
        self.commit("init")
        self.assertTrue(any("src/**" in e and "matches no file" in e for e in self.lint().errors))

    def test_glob_matching_something_passes(self):
        self.write("src/a.py", "x\n")
        self.claude("# demo\n\n## Layout\n<!-- covers: src/** -->\n- `src/a.py`: the code.\n")
        self.commit("init")
        self.assertEqual(self.lint().errors, [])

    def test_literal_glob_passes(self):
        self.write("install.py", "x\n")
        self.claude("# demo\n\n## Commands\n<!-- covers: install.py -->\n- Run: `python install.py`\n")
        self.commit("init")
        self.assertEqual(self.lint().errors, [])


class CoversCommentTest(ClaudemdCase):       # T4 -> AC4
    def test_malformed_comment_is_reported(self):
        self.claude("# demo\n\n## Layout\n<!-- not a covers comment -->\n- x\n")
        self.commit("init")
        self.assertTrue(any("malformed" in e for e in self.lint().errors))

    def test_bad_verified_date_is_reported(self):
        self.claude("# demo\n\n## Layout\n<!-- covers: install.py; verified: not-a-date -->\n- x\n")
        self.write("install.py", "x\n")
        self.commit("init")
        self.assertTrue(any("bad verified date" in e for e in self.lint().errors))

    def test_covers_with_verified_parses(self):
        self.write("install.py", "x\n")
        self.claude("# demo\n\n## Layout\n<!-- covers: install.py; verified: 2026-01-01 -->\n- x\n")
        self.commit("init")
        sections, problems = claudemd.parse(self.repo.joinpath("CLAUDE.md").read_text(encoding="utf-8"))
        self.assertEqual(problems, [])
        self.assertEqual(sections[0].covers, ["install.py"])
        self.assertEqual(sections[0].verified, dt.date(2026, 1, 1))


class LineCapTest(ClaudemdCase):             # T1 -> AC1
    def test_over_the_cap_is_an_error(self):
        body = "# demo\n\n## Layout\n" + "\n".join(f"- line {i}" for i in range(130)) + "\n"
        self.claude(body)
        self.commit("init")
        self.assertTrue(any("the cap is 120" in e for e in self.lint().errors))

    def test_at_the_cap_passes(self):
        body = "# demo\n\n## Layout\n" + "\n".join(f"- line {i}" for i in range(117)) + "\n"
        self.claude(body)
        self.assertEqual(len(body.splitlines()), 120)
        self.commit("init")
        self.assertFalse(any("cap" in e for e in self.lint().errors))


class StalenessTest(ClaudemdCase):           # T3 -> AC3
    SEC = "# demo\n\n## Layout\n<!-- covers: src/** -->\n- `src/a.py`: the code.\n"
    SEC_EDITED = "# demo\n\n## Layout\n<!-- covers: src/** -->\n- `src/a.py`: the code (edited).\n"

    def test_no_change_after_section_is_not_stale(self):
        self.write("src/a.py", "x\n")
        self.claude(self.SEC)
        self.commit("init", date="2026-01-01")
        r = self.lint()
        self.assertEqual(r.errors, [])
        self.assertEqual(r.stale, 0)

    def test_covered_file_changed_after_is_stale_but_not_an_error(self):
        self.write("src/a.py", "x\n")
        self.claude(self.SEC)
        self.commit("init", date="2026-01-01")
        self.write("src/a.py", "y\n")
        sha = self.commit("touch a.py", date="2026-01-05")
        r = self.lint()
        self.assertEqual(r.errors, [])
        self.assertEqual(r.stale, 1)
        self.assertTrue(any(sha[:7] in w for w in r.warnings))

    def test_editing_the_section_clears_staleness(self):
        self.write("src/a.py", "x\n")
        self.claude(self.SEC)
        self.commit("init", date="2026-01-01")
        self.write("src/a.py", "y\n")
        self.commit("touch a.py", date="2026-01-05")
        self.assertEqual(self.lint().stale, 1)
        self.claude(self.SEC_EDITED)
        self.commit("edit section", date="2026-01-10")
        self.assertEqual(self.lint().stale, 0)

    def test_verified_date_clears_staleness_without_editing_the_section(self):
        self.write("src/a.py", "x\n")
        self.claude(self.SEC)
        self.commit("init", date="2026-01-01")
        self.write("src/a.py", "y\n")
        self.commit("touch a.py", date="2026-01-05")
        self.assertEqual(self.lint().stale, 1)
        verified = self.SEC.replace("<!-- covers: src/** -->",
                                    "<!-- covers: src/**; verified: 2026-01-06 -->")
        self.claude(verified)
        self.commit("verify", date="2026-01-06")
        self.assertEqual(self.lint().stale, 0)

    def test_section_without_covers_is_never_stale(self):       # D5
        self.write("src/a.py", "x\n")
        self.claude("# demo\n\n## Layout\n- `src/a.py`: the code.\n")
        self.commit("init", date="2026-01-01")
        self.write("src/a.py", "y\n")
        self.commit("touch a.py", date="2026-01-05")
        self.assertEqual(self.lint().stale, 0)


class BumpVerifiedTest(ClaudemdCase):        # used by `seal retro` (AC6)
    def test_bumps_only_confirmed_sections_with_covers(self):
        text = ("# demo\n\n## Commands\n<!-- covers: install.py -->\n- x\n\n"
                "## Layout\n- no covers comment here.\n")
        sections, _ = claudemd.parse(text)
        new_text, bumped = claudemd.bump_verified(text, sections, {"Commands", "Layout"}, dt.date(2026, 2, 1))
        self.assertEqual(bumped, ["Commands"])
        self.assertIn("<!-- covers: install.py; verified: 2026-02-01 -->", new_text)

    def test_no_confirmed_sections_leaves_text_unchanged(self):
        text = "# demo\n\n## Commands\n<!-- covers: install.py -->\n- x\n"
        sections, _ = claudemd.parse(text)
        new_text, bumped = claudemd.bump_verified(text, sections, set(), dt.date(2026, 2, 1))
        self.assertEqual(bumped, [])
        self.assertEqual(new_text, text)


if __name__ == "__main__":
    unittest.main()
