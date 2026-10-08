"""Checks on skills/ (design §3): frontmatter, install, and that every
`vault.py` command and option a skill names actually exists in the CLI
parser (AC10/AC11 of #8, AC1/AC11 of #7)."""
from __future__ import annotations

import argparse
import re
import shlex
import sys
import tempfile
import unittest
from pathlib import Path

from tests.helpers import REPO

sys.path.insert(0, str(REPO / "agent_vault"))
sys.path.insert(0, str(REPO))
import lib  # noqa: E402
import vault  # noqa: E402
import install  # noqa: E402

SKILLS_DIR = REPO / "skills"
CODE_SPAN_RE = re.compile(r"`([^`]+)`")

# skill name -> (frontmatter trigger pattern, reference file next to SKILL.md, or None)
SKILLS = {
    "implement-story": (r"implement (#\d|issue \d)", "reviewer.md"),
    "plan-story": (r"plan (#\d|issue \d)", "plan-reviewer.md"),
    "retro": (r"retro (#\d|issue \d)", None),
    "issue": (r"file an issue for", None),
    "design": (r"design a \.\.\.", "design-reviewer.md"),
}


def read(skill: str, name: str) -> str:
    return (SKILLS_DIR / skill / name).read_text(encoding="utf-8")


def frontmatter(text: str) -> dict:
    lines = text.splitlines()
    if lines[0].strip() != "---":
        raise ValueError("SKILL.md must open with a '---' frontmatter block")
    end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    data = {}
    for ln in lines[1:end]:
        key, _, val = ln.partition(":")
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]     # a quoted YAML scalar, e.g. to keep a literal ' #' out of a comment
        data[key.strip()] = val
    return data


def vault_calls(text: str) -> list[str]:
    """The part after 'vault.py' in every code span that mentions it."""
    out = []
    for span in CODE_SPAN_RE.findall(text):
        if "vault.py" not in span:
            continue
        after = span.split("vault.py", 1)[1].strip()
        if after:
            out.append(after)
    return out


def subparsers_action(parser: argparse.ArgumentParser):
    return next((a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None)


def resolve(parser: argparse.ArgumentParser, tokens: list[str]):
    """Descend through nested subparsers while tokens name a subcommand.
    Returns (deepest parser, command path, tokens not consumed as a command name)."""
    path, i = [], 0
    while True:
        action = subparsers_action(parser)
        if action and i < len(tokens) and tokens[i] in action.choices:
            parser = action.choices[tokens[i]]
            path.append(tokens[i])
            i += 1
            continue
        break
    return parser, path, tokens[i:]


def is_placeholder(tok: str) -> bool:
    return "<" in tok or tok.startswith("[") or tok.startswith('"')


def check_call(top: argparse.ArgumentParser, call: str) -> list[str]:
    """Problems with one 'vault.py <call>' mention, or [] if it's fine."""
    tokens = shlex.split(call.replace("[", "").replace("]", ""))
    if not tokens or is_placeholder(tokens[0]):
        return []          # a generic illustration, e.g. "vault.py <command>"
    parser, path, rest = resolve(top, tokens)
    if not path:
        return [f"'{tokens[0]}' is not a vault.py command"]
    problems = []
    opts = set(parser._option_string_actions)
    for tok in rest:
        if tok.startswith("--"):
            name = tok.split("=", 1)[0]
            if name not in opts:
                problems.append(f"`{name}` is not accepted by `vault.py {' '.join(path)}`")
    return problems


class SkillFrontmatterTest(unittest.TestCase):  # T1 -> AC1 (#8, #7)
    def test_name_and_description(self):
        for skill, (pattern, _ref) in SKILLS.items():
            with self.subTest(skill=skill):
                fm = frontmatter(read(skill, "SKILL.md"))
                self.assertEqual(fm.get("name"), skill)
                desc = fm.get("description", "").lower()
                self.assertTrue(re.search(pattern, desc),
                                 f"{skill}: description should show a trigger matching {pattern!r}")


class SkillInstallTest(unittest.TestCase):  # T1 -> AC1 (#8, #7)
    def test_installs_with_marker(self):
        with tempfile.TemporaryDirectory() as td:
            claude = Path(td) / ".claude"
            report: list[str] = []
            install.install_skills(claude, report)
            for skill, (_pattern, ref) in SKILLS.items():
                with self.subTest(skill=skill):
                    dest = claude / "skills" / skill
                    self.assertTrue((dest / "SKILL.md").is_file())
                    if ref:
                        self.assertTrue((dest / ref).is_file())
                    self.assertTrue((dest / install.SKILL_MARKER).exists())
                    self.assertTrue(any(skill in r for r in report))


class VaultCommandsTest(unittest.TestCase):  # T2 -> AC10 (#8), AC11 (#7)
    def test_every_command_and_option_exists(self):
        top = vault.build_parser()
        problems, calls = [], []
        for skill, (_pattern, ref) in SKILLS.items():
            for name in ("SKILL.md", *([ref] if ref else [])):
                found = vault_calls(read(skill, name))
                calls += found
                problems += [f"{skill}/{name}: {p}" for p in (p for call in found for p in check_call(top, call))]
        self.assertGreaterEqual(len(calls), 3,
                                 "expected several 'vault.py ...' mentions to check; found none — "
                                 "did the extraction regex break, or did the skills stop naming any?")
        self.assertEqual(problems, [])
        for skill, (_pattern, ref) in SKILLS.items():
            with self.subTest(skill=skill):
                found = sum(len(vault_calls(read(skill, name))) for name in ("SKILL.md", *([ref] if ref else [])))
                self.assertGreaterEqual(found, 1, f"{skill}: expected at least one 'vault.py ...' mention")


class AssertMentionsMixin:
    def assertMentions(self, *needles: str, text: str | None = None):
        # collapse wrapped-prose whitespace so a phrase split across a line
        # wrap by markdown formatting still matches
        text = re.sub(r"\s+", " ", (text if text is not None else self.skill).lower())
        for n in needles:
            self.assertIn(re.sub(r"\s+", " ", n.lower()), text, f"expected {n!r} in the skill files")


class ImplementStoryProcedureTest(AssertMentionsMixin, unittest.TestCase):  # T3 -> AC2-AC9 (#8)
    def setUp(self):
        self.skill = read("implement-story", "SKILL.md")
        self.reviewer = read("implement-story", "reviewer.md")

    def test_start_resolves_repo_issue_and_gates_on_preflight(self):  # AC2
        self.assertMentions("vault.py preflight", "git config --get remote.origin.url",
                             "vault.py branch")

    def test_per_step_commits_without_review(self):  # AC1
        self.assertMentions("(#<issue>)", "one commit per step", "run the tests relevant to this step",
                             "a step is done when its commit exists")
        for text in (self.skill, self.reviewer):
            self.assertNotIn("step <s> reviewed", text.lower())
            self.assertNotIn("after every step", text.lower())

    def test_single_end_review_with_three_round_limit(self):  # AC2, AC3
        self.assertMentions("`full check:`", "whole diff", "every acceptance criterion",
                             "re-run `full check:`", "review the whole diff again",
                             "third review still has a major finding, stop and ask",
                             "never start a fourth review")

    def test_minors_left_open_and_listed_in_the_pr(self):  # AC4
        self.assertMentions("→ left open", "\"left open\" list", "(major|minor)", "fixed in", "won't fix")
        self.assertMentions("every major finding and at most three minor", text=self.reviewer)

    def test_pr_review_fixes_get_one_review(self):  # AC5
        self.assertMentions("review once, covering all the fixes", "pr review:", "`pr review <k>:`",
                             "starting again at 1")

    def test_deviations_and_stop_and_ask(self):  # AC5
        self.assertMentions("## deviations", "stop and ask")

    def test_finish_runs_full_check_final_review_and_opens_pr(self):  # AC6
        self.assertMentions("full check", "whole diff", "## verification",
                             "## discoveries", "vault.py open-pr")

    def test_lint_runs_with_full_check_before_every_review(self):  # T6 -> AC6 (#33)
        text = re.sub(r"\s+", " ", self.skill)
        finish = text[text.index("## Finish"):text.index("## Address PR review")]
        pr_review = text[text.index("## Address PR review"):text.rindex("## Stop and ask if")]
        self.assertEqual(finish.count("`vault.py lint`"), 2)           # first review and each re-review
        self.assertLess(finish.index("`vault.py lint`"), finish.index("Review the whole diff"))
        self.assertIn("Request no review while either fails", finish)
        self.assertLess(pr_review.index("`vault.py lint`"), pr_review.index("review once"))

    def test_address_pr_review(self):  # AC7
        self.assertMentions("address pr review", "pr review:", "gh pr view", "--paginate",
                             "leave the impl note open")
        self.assertMentions("no way to choose a subagent's model", "never the implementer's own model",
                             text=self.reviewer)

    def test_resume_from_commits_and_impl_note(self):  # AC8
        self.assertMentions("## resume", "git log --oneline <base>..head", "impl note")

    def test_never_merges_pushes_main_edits_plan_or_installs(self):  # AC9
        self.assertMentions("never merge", "never push to `main`", "never edit a sealed plan",
                             "never run `install.py`")


class ImplNoteFormatTest(unittest.TestCase):  # T3 -> AC3, AC4, AC7 (schema fidelity) (#8)
    def test_finding_line_convention_matches_the_fixed_schema(self):
        """The tags the skill uses (Review <k>/PR review, and the old Step/Final review) still produce a
        line lib.py's own IMPL-REV format actually accepts — not just something that
        looks plausible in prose."""
        samples = [
            "- R1 (major): Step 1: example finding → fixed in 1234567",
            "- R2 (minor): Final review: example finding → won't fix: not worth it",
            "- R3 (minor): PR review: example finding → fixed in abcdef1",
            "- R4 (minor): Review 1: example finding → left open",
        ]
        for line in samples:
            self.assertTrue(any(rx.match(line) for _, rx in lib.LINE_FORMATS["IMPL-REV"]),
                             f"{line!r} should match lib.py's IMPL-REV format")

    def test_left_open_is_minor_only_and_in_the_rule_text(self):  # T6 -> AC6
        self.assertIn("left open", lib.RULES["IMPL-REV"][1])
        major = "- R5 (major): Review 1: example finding → left open"
        self.assertFalse(any(rx.match(major) for _, rx in lib.LINE_FORMATS["IMPL-REV"]))


class PlanStoryProcedureTest(AssertMentionsMixin, unittest.TestCase):  # T3 -> AC2-AC10 (#7)
    def setUp(self):
        self.skill = read("plan-story", "SKILL.md")
        self.reviewer = read("plan-story", "plan-reviewer.md")

    def test_research_before_asking(self):  # AC2
        self.assertMentions("## research", "before asking the user anything",
                             "gh issue view", "don't count as requirements",
                             "other open issues", "spec sections",
                             "read the repo's `claude.md`", "project overview note",
                             "the code the issue will touch")

    def test_interview_open_ended_with_reasoned_recommendations(self):  # AC8
        self.assertMentions("## interview", "plain prose", "state the fact that raised it",
                             "no list of options", "only when you're confident",
                             "always with the reason", "at most 4",
                             "would change the plan is still ambiguous")
        for gone in ("recommended one first", "trade-offs"):
            self.assertNotIn(gone, re.sub(r"\s+", " ", self.skill.lower()))

    def test_draft_validated_with_user_decisions(self):  # AC4
        self.assertMentions("vault.py new plan", "vault.py validate",
                             "no errors or warnings", "(user decision)")

    def test_plan_reviewer_runs_on_opus_with_own_prompt_and_fixed_format(self):  # AC5
        self.assertMentions("see `plan-reviewer.md`", "fix what you can",
                             "goes into the summary instead of being silently dropped")
        self.assertMentions('model: "opus"', "exercise it", "neighbouring issue's scope",
                             "go beyond the issue itself", text=self.reviewer)
        self.assertMentions("no findings.", "(major)", "(minor)", text=self.reviewer)

    def test_summary_never_asks_to_read_the_raw_plan(self):  # AC6
        self.assertMentions("## summary", "plain-language summary",
                             "never asking the user to read the raw plan",
                             "the user's decisions from the interview",
                             "the skill's own judgment calls, flagged for the user to check",
                             "what's out of scope,",
                             "when the implementer must stop and ask")

    def test_summary_ends_with_a_pointed_question(self):  # AC9
        section = re.sub(r"\s+", " ", self.skill.split("## Summary")[1].split("\n## ")[0])
        last = section.split("- ")[-1].lower()
        self.assertIn("one pointed question", last)
        self.assertIn("riskiest judgment call", last)
        self.assertMentions("answered the summary's closing question", "explicitly approved it")

    def test_approval_seals_only_after_explicit_approval(self):  # AC7
        self.assertMentions("## approval", "vault.py seal plan", "explicit approval",
                             "change list")

    def test_issue_hygiene_approved_before_posting(self):  # AC8
        self.assertMentions("## issue hygiene",
                             "discoveries that belong to a different, unplanned issue",
                             "criteria that turned out impossible",
                             "propose the exact wording to the user before doing anything",
                             "post github edits only after the user approves that wording",
                             "before sealing, so the sealed plan matches the issue")

    def test_handoff_suggests_worktree_and_never_branches(self):  # AC9
        self.assertMentions("## handoff", "a fresh session on", "sonnet", "git worktree",
                             '"implement #<issue>"', "never creates the branch")

    def test_never_writes_code_branches_or_edits_sealed_plan(self):  # AC10
        self.assertMentions("## never", "never write code", "never create a branch",
                             "never edit a sealed plan")


class RetroProcedureTest(AssertMentionsMixin, unittest.TestCase):  # T6 -> AC1-AC5, AC7 (#9)
    def setUp(self):
        self.skill = read("retro", "SKILL.md")

    def test_preflight_needs_a_pr_and_the_right_branch(self):  # AC1, AC7
        self.assertMentions("## preflight", "no `pr:`", "check out the impl note's", "branch")

    def test_audit_runs_lint_new_retro_and_reads_context(self):  # AC2
        self.assertMentions("vault.py claudemd-lint", "vault.py new retro", "resume",
                             "gh pr diff", "the issue", "the sealed plan", "the impl note")

    def test_audits_all_three_docs_with_one_line_each(self):  # AC2, AC6
        self.assertMentions("claude.md", "readme.md", "project overview", "confirmed",
                             "rewritten", "removed", "committed and pushed",
                             "never gets in-flight work", "single-repo facts")

    def test_asks_about_restated_facts(self):  # AC3
        self.assertMentions("restate", "the code", "another doc", "the design")

    def test_follow_ups_sorted_with_approval(self):  # AC4
        self.assertMentions("issue #<n> created", "issue #<n> amended", "idea [[<idea>]]",
                             "dropped:", "approve", "vault.py idea add", "vault.py idea drop",
                             "vault.py ideas --project")

    def test_seal_after_validate_and_summary_approval(self):  # AC5
        self.assertMentions("vault.py validate", "plain-language summary", "vault.py seal retro")

    def test_merging_is_seal_retros_alone_and_nothing_else_pushes_main_edits_sealed_or_installs(self):  # T6 -> AC6 (#51)
        text = " ".join(self.skill.lower().split())
        self.assertNotIn("never merge a pr", text)
        never = " ".join(re.search(r"^## Never\n(.*?)^## ", self.skill, re.S | re.M).group(1).lower().split())
        self.assertIn("merging is `seal retro`'s job alone", never)
        self.assertIn("never `gh pr merge`", never)
        self.assertIn("github's merge button", never)
        self.assertMentions("never push to `main`", "never edit a sealed", "never run `install.py`")
        self.assertIn("never merge a pr", " ".join(read("implement-story", "SKILL.md").lower().split()))

    def test_summary_approval_is_the_merge_approval_and_a_backfill_merges_nothing(self):  # T6 -> AC6 (#51)
        seal = " ".join(re.search(r"^## Seal\n(.*?)^## ", self.skill, re.S | re.M).group(1).lower().split())
        self.assertIn("names the pr sealing merges; approving the summary approves that merge", seal)
        backfill = " ".join(re.search(r"^## Backfill[^\n]*\n(.*?)^## ", self.skill, re.S | re.M)
                            .group(1).lower().split())
        self.assertIn("a backfill's approval merges nothing", backfill)
        self.assertIn("never merges a closed issue", backfill)
        stop = " ".join(re.search(r"^## Stop and ask if\n(.*)", self.skill, re.S | re.M).group(1).lower().split())
        self.assertIn("refuses over the pr's checks or head, or github refuses the merge", stop)
        self.assertIn("never merge any other way", stop)
        desc = frontmatter(self.skill)["description"].lower()
        self.assertIn("squash-merges the pr", desc)

    def test_closed_issue_backfill_path(self):  # AC7
        self.assertMentions("## backfill", "closed", "no `branch:`", "dirty tree", "upstream")

    def test_stop_and_ask(self):
        self.assertMentions("## stop and ask if")


class SealRetroMergeDocsTest(unittest.TestCase):  # T7 -> AC7 (#51)
    def squashed(self, text):
        return " ".join(text.lower().split())

    def test_readme_git_conventions_say_seal_retro_merges(self):
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        section = self.squashed(re.search(r"^## Git conventions\n(.*?)^## ", readme, re.S | re.M).group(1))
        self.assertIn("only the owner merges", section)
        self.assertIn("`vault.py seal retro`, which squash-merges the pr itself", section)

    def test_claude_md_retro_line_says_seal_retro_merges(self):
        claude = (REPO / "CLAUDE.md").read_text(encoding="utf-8")
        line = next(ln for ln in claude.splitlines() if ln.startswith("- `skills/retro/`"))
        low = self.squashed(line)
        self.assertIn("squash-merges that pr itself", low)
        self.assertIn("otherwise only the user merges", low)


class IssueProcedureTest(AssertMentionsMixin, unittest.TestCase):  # T9, T10 -> AC5, AC6 (#27)
    def setUp(self):
        self.skill = read("issue", "SKILL.md")

    def test_description_names_both_triggers(self):  # AC5
        desc = frontmatter(self.skill)["description"].lower()
        self.assertIn("file an issue for", desc)
        self.assertIn("turn this idea into an issue", desc)

    def test_drafts_asks_shows_and_creates_only_after_approval(self):  # AC5
        self.assertMentions("vault.py schema", "open-ended questions in prose", "never multiple choice",
                            "the exact title and the exact body", "explicit approval",
                             "vault.py issue create", "--idea", "## stop and ask if")

    def test_never_runs_gh_issue_create_or_edits_an_issue(self):  # AC5
        self.assertMentions("## never", "never run `gh issue create`", "never edit an existing issue")

    def test_retro_and_plan_story_create_issues_only_through_the_command(self):  # AC6
        for name in ("retro", "plan-story"):
            text = read(name, "SKILL.md")
            with self.subTest(skill=name):
                self.assertNotIn("gh issue create", text)
                self.assertMentions("vault.py issue create", "exact title and body", text=text)


class RetroFollowFormatTest(unittest.TestCase):  # T6 -> AC4 (schema fidelity) (#9)
    def test_sample_lines_match_the_fixed_schema(self):
        """The four follow-up shapes the skill uses still produce lines lib.py's own
        RETRO-FOLLOW format actually accepts."""
        samples = [
            "- issue #12 created",
            "- issue #13 amended",
            "- idea [[2026-09-27-example]]",
            "- dropped: not needed",
        ]
        for line in samples:
            self.assertTrue(any(rx.match(line) for _, rx in lib.LINE_FORMATS["RETRO-FOLLOW"]),
                             f"{line!r} should match lib.py's RETRO-FOLLOW format")

    def test_stop_and_ask_covers_a_pr_that_no_longer_closes_the_issue(self):  # T5 -> AC5 (#35)
        text = read("retro", "SKILL.md")
        section = re.search(r"^## Stop and ask if\n(.*)", text, re.S | re.M).group(1)
        low = " ".join(section.lower().split())
        self.assertIn("`seal retro` refuses because the pr will no longer close the issue", low)
        self.assertIn("put the fix to the user", low)
        self.assertIn("closing the issue by hand", low)

    def test_left_open_findings_sorted_with_reference(self):  # T5 -> AC5 (#32)
        text = read("retro", "SKILL.md")
        section = re.search(r"^## Follow-ups\n(.*?)^## ", text, re.S | re.M).group(1)
        low = " ".join(section.lower().split())
        self.assertIn("left open", low)
        self.assertIn("`seal retro` refuses", low)
        refs = re.findall(r"`(- [^`]*\(R\d+(?:, R\d+)*\))`", section)
        self.assertTrue(refs, "the Follow-ups section should show a (R<n>) reference example")
        for line in refs:
            self.assertTrue(any(rx.match(line) for _, rx in lib.LINE_FORMATS["RETRO-FOLLOW"]),
                            f"{line!r} should match lib.py's RETRO-FOLLOW format")
        self.assertLessEqual(len(text.splitlines()), 235)


class DecisionRecordsProcedureTest(AssertMentionsMixin, unittest.TestCase):  # T8 -> AC9, AC10 (#36)
    def setUp(self):
        self.plan_story = read("plan-story", "SKILL.md")
        self.reviewer = read("plan-story", "plan-reviewer.md")
        self.retro = read("retro", "SKILL.md")
        self.guide = (REPO / "templates" / "vault-CLAUDE.md").read_text(encoding="utf-8")

    def section(self, text, heading):
        return re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.S | re.M).group(1)

    def test_plan_story_reads_active_records_in_research(self):          # AC9
        self.assertMentions("`vault.py decisions --project <id>`", "active decision records",
                            "must not contradict one unless a decision in it replaces that record",
                            text=self.section(self.plan_story, "Research"))

    def test_plan_story_marks_lasting_and_replacing_decisions(self):     # AC9
        self.assertMentions('" (lasting)"', "`replaces [[<record>]]`", "citing a record without reversing it",
                            text=self.section(self.plan_story, "Draft"))
        self.assertIn(lib.LASTING_SUFFIX, " (lasting)")
        self.assertTrue(any(rx.match("- D1: x, because y (user decision) (lasting)")
                            for _, rx in lib.LINE_FORMATS["PLAN-D"]))

    def test_plan_story_summary_lists_lasting_decisions_before_the_closing_question(self):     # AC9
        summary = re.sub(r"\s+", " ", self.section(self.plan_story, "Summary").lower())
        self.assertIn("the lasting decisions", summary)
        self.assertLess(summary.index("the lasting decisions"), summary.index("one pointed question"))

    def test_plan_reviewer_gets_records_and_counts_an_unnamed_contradiction_as_major(self):     # AC9
        self.assertMentions("active decision records", "`vault.py decisions --project <id>`",
                            "`replaces [[<record>]]`", text=self.reviewer)
        major = re.sub(r"\s+", " ", self.reviewer.split("`major`:")[1].split("`minor`:")[0].lower())
        self.assertIn("active decision record that the draft doesn't name as replaced", major)

    def test_retro_records_decisions_before_sealing(self):               # AC10
        self.assertMentions("vault.py decision add", "--replaces", "`- decision [[<record>]]`",
                            "ending in \" (lasting)\"", "`seal retro` refuses until every lasting plan decision",
                            "--source \"plan <repo>#<issue> D<k>\"", "--source \"retro <repo>#<issue>\"",
                            text=self.retro)
        self.assertLess(self.retro.index("## Decisions"), self.retro.index("## Seal"))

    def test_retro_audits_standing_decisions_as_generated(self):         # AC10
        audit = re.sub(r"\s+", " ", self.section(self.retro, "Audit").lower())
        self.assertIn("`## standing decisions` is generated", audit)
        self.assertIn("audit it as `confirmed`", audit)

    def test_decision_follow_up_form_matches_the_format(self):           # AC8, AC10
        for line in ("- decision [[demo-d1-keep-it-small]]", "- decision [[demo-d1-keep-it-small]] (R2)"):
            self.assertTrue(any(rx.match(line) for _, rx in lib.LINE_FORMATS["RETRO-FOLLOW"]), line)

    def test_vault_guide_lists_the_folder_and_both_commands(self):       # AC10
        self.assertMentions("`Decisions/<project>/<project>-d<n>-<slug>.md`", "decision add", "{{CLI}} decisions",
                            "--replaces", text=self.guide)


class DesignSkillTest(AssertMentionsMixin, unittest.TestCase):  # T9 -> AC10-AC13 (#37)
    def setUp(self):
        self.skill = read("design", "SKILL.md")
        self.reviewer = read("design", "design-reviewer.md")

    def test_triggers(self):                                          # AC10
        desc = frontmatter(self.skill)["description"].lower()
        for phrase in ("design a", "let''s design", "combine these ideas"):
            self.assertIn(phrase, desc)

    def test_research_interview_and_resume(self):                     # AC10
        self.assertMentions("runs on opus", "active decision records", "open ideas", "open issues",
                            "outside the codebase", "web search", "open-ended", "states the fact that raised it",
                            "as many rounds as", "challenge assumptions", "resume, don't restart",
                            "a draft there is the design in progress")

    def test_rigor_rules(self):                                       # AC11
        self.assertMentions("at least two genuinely different options", "trade-offs", "every decision has a reason",
                            "the technology the design needs is named in `chosen design`", "mermaid",
                            "risks and unknowns stay in `open questions`", "never decisions")

    def test_review_summary_and_sealing(self):                        # AC12
        self.assertMentions("independent reviewer subagent on opus", "never silently fall back",
                            "one pointed question", "create its note now, after approval and before sealing",
                            "vault.py seal design", "vault.py new project")
        self.assertMentions("`model: \"opus\"`", "never the drafting session's own model", text=self.reviewer)
        self.assertMentions("goal that no story delivers", "can't be built or shipped alone",
                            "active decision record", "open question, risk or unknown written up as a decision",
                            "genuinely different", "(major)", "(minor)", "no findings.", text=self.reviewer)

    def test_filing_the_stories(self):                                # AC13
        self.assertMentions("[[<design>]] s<n>", "under source", "depends on: s<n>", "exactly as they will be posted",
                            "vault.py issue create", "never run `gh issue create`", "vault.py design stories",
                            "not filed", "a project with no repo yet waits", "filing later", "file the stories of")

    def test_feature_before_stories(self):                            # T11 -> AC10 (#56)
        filing = self.skill.split("## File the stories")[1].split("## Filing later")[0]
        self.assertLess(filing.index("--feature <design>"), filing.index("--parent <n>"))
        self.assertMentions("the design's feature", "no story number", "the repo the user names",
                            "--parent <feature repo>#<n>", "neither the feature nor its stories",
                            text=filing)
        later = self.skill.split("## Filing later")[1].split("## Stop and ask if")[0]
        self.assertMentions("no feature yet", "create the feature first", "even when every story is already filed",
                            text=later)

    def test_plan_story_stops_on_a_feature(self):                     # T11 -> AC10 (#56)
        plan = read("plan-story", "SKILL.md")
        research = plan.split("## Research")[1].split("## Interview")[0]
        self.assertMentions("--json title,body,comments,labels", "labelled `feature`", text=research)
        self.assertMentions("the issue is labelled `feature`", text=plan.split("## Stop and ask if")[1])


class DesignDocsTest(AssertMentionsMixin, unittest.TestCase):  # T10 -> AC14 (#37)
    def test_plan_story_reads_the_design(self):
        self.assertMentions("links a design", "`[[<design>]] s<n>`", "agent/designs/<project>/", "the story `s<n>`",
                            text=read("plan-story", "SKILL.md"))

    def test_vault_guide_names_designs(self):
        guide = (REPO / "templates" / "vault-CLAUDE.md").read_text(encoding="utf-8")
        self.assertMentions("designs/<project>/<project>-design-<n>-<slug>.md", "new design <project>",
                            "seal design <design>", "design stories <design>", "design <design> d<k>",
                            "(by convention)", "the `design` skill", "file the stories of", text=guide)
        self.assertMentions("start a fresh session on opus", text=guide)

    def test_repo_claude_md_names_the_skill(self):
        self.assertMentions("`skills/design/`", "`skills/design/design-reviewer.md`",
                            text=(REPO / "CLAUDE.md").read_text(encoding="utf-8"))

    def test_docs_name_features(self):                                # T12 -> AC11 (#56)
        guide = (REPO / "templates" / "vault-CLAUDE.md").read_text(encoding="utf-8")
        self.assertMentions("--feature", "--parent <n>|<repo>#<n>]` (creates", "{{CLI}} features", "no feature yet",
                            "the `planned`, `feature` and `bug` labels", text=guide)
        self.assertMentions("--feature", "--parent", "vault.py features", text=(REPO / "CLAUDE.md").read_text(encoding="utf-8"))
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        self.assertMentions("2.94.0", text=readme.split("## Install")[1].split("## Git conventions")[0])
        self.assertMentions("--feature", "--parent", "under its feature issue",
                            text=readme.split("## Git conventions")[1])


class NoPrPathTest(AssertMentionsMixin, unittest.TestCase):  # T8 -> AC8 (#66)
    def test_plan_story_declares_it(self):
        self.assertMentions("pr: none", "Files:", "whether the issue ends in a pull request",
                            text=read("plan-story", "SKILL.md"))

    def test_implement_story_has_the_route(self):
        self.assertMentions("pr: none", "No-PR plan", "Step <n>: done", "no diff review",
                            "the retro", text=read("implement-story", "SKILL.md"))

    def test_retro_drafts_and_posts_the_closing_comment(self):
        self.assertMentions("pr: none", "Closing comment", "closes the issue", "posts it verbatim",
                            text=read("retro", "SKILL.md"))


if __name__ == "__main__":
    unittest.main()

