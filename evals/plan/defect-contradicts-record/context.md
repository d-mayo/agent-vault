base_sha: 285563f

# Context

Excerpts at the plan's base commit.

### `skills/implement-story/SKILL.md` (whole file)
`````markdown
---
name: implement-story
description: 'Take a GitHub issue with a sealed agent-vault plan from its branch to an open pull request, or address PR review comments already requested on one. Implements step by step with one commit and the relevant tests per step, then one independent review of the whole diff at the end, keeps the impl note current, and resumes cleanly in a new session. Use for requests like "implement #9", "implement issue 9", "continue implementing agent-vault#9", or "address the review on #9".'
---

# implement-story

Turns a sealed plan into commits, review findings and an open pull request,
recording deviations and findings in the issue's impl note as it goes. The
reviewer's brief and findings format are in `reviewer.md`, next to this file.

Every `vault.py <command>` below means the exact CLI string in the current
vault's `CLAUDE.md` (the line under "CLI after install"); it always ends in
`vault.py`, so `vault.py preflight <repo> <issue>` means "run that string
followed by `preflight <repo> <issue>`". `<repo>` is always the plain repo
name (e.g. `agent-vault`), never `owner/name`.

## Never

- Never merge a PR, and never push to `main`.
- Never edit a sealed plan. If it turns out to be wrong, stop and ask.
- Never run `install.py`.

## Approvals, pings and the closing summary

- Every yes/no approval this skill asks for is one `AskUserQuestion` prompt
  (below, "the approval prompt") with exactly three options: `Yes`, `No` and
  `Hold on`. Only `Yes` proceeds.
  - `Hold on`: stop, take up what the user wants to discuss first, and ask the
    same prompt again once that's settled.
  - `No`: change nothing, ask in prose why, and work through the reason before
    asking again.
  - A free-text answer (the prompt's own "Other" choice) that starts with a
    clear yes counts as `Yes`, unless it also asks for a change: then make the
    change and ask again, since what is approved must be what was shown. Any
    other free-text answer counts as `Hold on`.
  - Without `AskUserQuestion`, ask in prose with the same three answers; only
    an explicit Yes counts.
  - Questions that aren't approvals (interviews, clarifying questions, a
    summary's closing question) stay open-ended prose.
- When this skill's stage is finished and the next stage is the user's to
  start, send one push notification (the `PushNotification` tool): a single
  line under 200 characters naming what finished and what the user starts
  next. Send none for anything else. If the tool isn't available or doesn't
  send, carry on.
- The final response of a finished stage ends with at most three bullets, each
  one short sentence: the facts the user most needs to know, with nothing after
  them.

## Which procedure applies

- The sealed plan's frontmatter says `pr: none`: go to **No-PR plan**.
- The impl note already has a `pr:`, or the request is about review comments
  or requested changes rather than implementing: go to **Address PR review**.
- Otherwise: go to **Start** — it reads the plan regardless of whether a
  branch already exists, and its own step 3 hands off to **Resume** for
  whatever's already committed. Never jump straight to **Resume**: it has no
  preflight check and doesn't read the plan or check out anything.

## Resolve the repo and issue

- Vault and CLI: `~/.claude/agent-vault.json`'s `vault` field is the vault
  path; the exact `vault.py` invocation is the line under "CLI after
  install" in `<vault>/CLAUDE.md`. A repo session's own startup context
  doesn't carry either.
- Repo: `git config --get remote.origin.url` in the current clone gives a
  URL such as `git@github.com:owner/name.git` or `https://github.com/owner/name`;
  strip the host and any trailing `.git` to get `owner/name`. The part after
  the `/` is `<repo>` for every command below.
- Issue: the number the user gave, or, on an existing branch, the `<n>` in
  `<type>/<n>-<slug>` (`git branch --show-current`).
- If neither gives a number, or the clone has no GitHub `origin`, stop and ask.
- `<base>`, used below: `git fetch origin <default>` (the repo's default
  branch), then `git merge-base HEAD origin/<default>`. Always this, never
  the plan's `base_sha` — by the time you implement, other sealed plans may
  already have merged past it.

## Start

1. Run `vault.py preflight <repo> <issue>`. If it fails — no sealed plan, the
   issue is closed, or the issue changed since the plan was sealed — stop and
   report the reason; don't improvise a plan of your own. If it passes but
   prints warnings (`base_sha` far behind, or commits since sealing touching
   files the plan names), read them: if the plan now rests on something no
   longer true, stop and ask instead of implementing around it.
2. Read the sealed plan (`Agent/Work/<repo>/<repo>-<issue>-plan.md`) and the
   impl note (`-impl.md`) in the vault, and the repo's `CLAUDE.md`.
3. Check whether work already started: the impl note's `branch:` field, then
   (if that's empty) a local or remote branch matching `<type>/<issue>-*`
   (`git branch --list` and `git ls-remote --heads origin`) — a branch can
   exist without being checked out, or only on GitHub in a fresh clone.
   - None found: make sure the working tree is clean, then run
     `vault.py branch <repo> <issue> [--type <type>] [--slug <slug>]`.
     The type comes from the issue's `bug` label: a bug gets `fix/`, so
     leave `--type` out; pass `--type` only for a non-bug whose plan
     clearly calls for something other than `feat`, and never `fix` or
     `hotfix`. `--slug` overrides the default slug. This checks out the new
     branch and records it in the impl note.
   - Found: fetch and check it out (`git switch <branch>`, or
     `git switch -c <branch> origin/<branch>` if it isn't local yet). If the
     impl note's `branch:` field is still empty (e.g. the branch was made by
     hand), record it there now, directly — `open-pr` needs it later and
     nothing else will set it. Then go to **Resume**.

## Resume (a step, or the whole issue, already has commits)

1. `git log --oneline <base>..HEAD` to see which steps already have a
   `<type>(<scope>): <step title> (#<issue>)` commit.
2. A step is done when its commit exists. Continue from the first step that
   has neither a commit nor a recorded deviation covering it. If every step
   is committed, go to **Finish**; its review rounds are counted from the
   impl note's `## Verification` `Review <k>:` lines (`PR review <k>:` lines
   belong to **Address PR review**).

## Per step

Work through the plan's `## Steps` in order. For each step (numbered `<s>`):

1. **Implement exactly what its `Files:` and `Do:` say.** If you touch a file
   the step doesn't list, or do something it doesn't ask for, that's a
   deviation: add a line under the impl note's `## Deviations`
   (`- Step <s>: <what, and why>`) as soon as you do it, not at the end.
2. Check the step's own "Stop and ask if" conditions and the plan's
   `## Stop and ask if` section. If one applies, or the step turns out to
   rest on something that's no longer true, stop and ask the user instead of
   guessing past it.
3. Run the tests relevant to this step (not necessarily the whole suite
   yet). If any fail, fix the step before committing — a failing test is
   never part of a step's checkpoint commit.
4. Commit: `<type>(<scope>): <step title> (#<issue>)` — `<type>` is one of
   `feat fix chore docs refactor test perf hotfix`, `<scope>` is the area the
   step touches, and `<step title>` is the step's own title. One commit per
   step: never squash two steps together, never split one step across
   commits. There is no review per step; the review comes in **Finish**.

## Finish

Once every step is committed:

1. Run the plan's `Full check:` command, from its `## Tests` section, and
   `vault.py lint` (`claudemd-lint`, then the repo's `Lint:` command). If
   either fails, fix it first (a lint failure in its own commit), and record
   what broke under `## Discoveries` (or as a deviation, if the fix touched
   files beyond the step that broke it). Request no review while either fails.
2. Review the whole diff, following `reviewer.md`'s "Prompt" section
   (`git diff <base>..HEAD`, every acceptance criterion, the repo
   `CLAUDE.md`). Record it under `## Verification` as `- Review <k>: R<n>-R<n2>`
   or `- Review <k>: no findings`, and each finding under `## Review findings`,
   numbered on from the highest existing `R<n>`:
   `- R<n> (major|minor): Review <k>: <finding> → fixed in <sha>`,
   `→ won't fix: <reason>`, or, for a minor one you're leaving, `→ left open`.
3. Fix every `major` finding, each fix its own commit (no amending: the
   whole diff is reviewed anyway), then re-run `Full check:` and `vault.py lint`, and review the
   whole diff again (`Review <k+1>`). Repeat until a review has no major
   finding. If the third review still has a major finding, stop and ask the
   user; never start a fourth review.
4. Minor findings are not fixed here unless a major fix already touches
   them; record them as `→ left open`. The reviewer reports at most three.
5. Fill in `## Verification` (each command you ran and what it showed,
   alongside the `Review <k>:` lines) and `## Discoveries` (anything worth
   the retro that isn't already a deviation or a finding). Keep both terse —
   impl notes cap at 80 body lines.
6. Write a short summary of the impl note (goal, what changed, notable
   findings — not the raw note) to a temp file *outside* the clone (e.g. your
   scratchpad directory); an untracked file inside the clone would make the
   next command's clean-tree check fail. End it with a "Left open" list of
   the minor findings still open, for the user to decide. Open the PR:
   `vault.py open-pr <repo> <issue> --body-file <file>`. `Closes #<issue>` is
   added automatically. Then send the push notification: the user reviews the
   PR and runs `retro #<issue>` next.
7. Leave the impl note `status: open`; it's sealed later, by the retro.

## No-PR plan

A plan with `pr: none` changes no repo file (`seal plan` guarantees every
step's `Files:` is `none`), so there is no branch, no commit, no diff
review and no pull request. `branch` and `open-pr` refuse such an issue.

1. Run `vault.py preflight <repo> <issue>`, as in **Start**, and read the
   plan and impl note.
2. Do each step's `Do:` in order (vault notes, GitHub work, anything the
   plan describes), keeping to the plan's "Stop and ask if" lines. After
   each step, add `- Step <n>: done` to the impl note's `## Verification`
   straight away: with no commits, that line is the resume marker. A
   resumed session continues from the first step with no such line.
3. When every step is done, run the plan's `Full check:` if it names one
   that applies, and record what you ran and what it showed under
   `## Verification`, and anything worth the retro under `## Discoveries`.
   Skip the diff review: there is no diff.
4. Leave the impl note `status: open`, and tell the user the next step is
   the retro (`retro #<issue>`), whose sealing closes the issue, and send the
   push notification naming `retro #<issue>` as the next stage.

## Address PR review

Asked to act on review comments for an issue that already has an open PR
(the impl note's `pr:`):

1. From inside the clone, on the issue's branch: read both
   `gh pr view --json reviews,comments` (review summaries and general PR
   comments) **and**
   `gh api --paginate repos/<owner>/<repo>/pulls/<pr>/comments` (inline line
   comments) — neither alone has everything that was requested.
2. For each requested change: if it conflicts with the plan or you're unsure
   how to resolve it, stop and ask the user first (see "Stop and ask if")
   rather than recording a resolution before you have one. Otherwise fix it
   in its own commit, or decline it with a reason. If one comment's fix
   touches several files or changes behaviour, tell the reviewer which
   commit to look at first; it gets no extra review.
3. Run `Full check:` and `vault.py lint`, fixing failures first as in **Finish**
   step 1; then review once, covering all the fixes: `git diff <tip>..HEAD`, where `<tip>`
   is the branch tip before the first fix, with the request texts supplied
   (see `reviewer.md`). Same rules as **Finish** step 3: fix every major
   finding, re-run `Full check:` and `vault.py lint`, and review again, at most three reviews,
   then stop and ask. Record each finding under `## Review findings` as
   `- R<n> (major|minor): PR review: <finding> → fixed in <sha>`,
   `→ won't fix: <reason>` or (minor only) `→ left open`, and the review under
   `## Verification` (`- PR review <k>: R<n>-R<n2>` or `- PR review <k>: no
   findings`); its rounds are counted separately from **Finish**'s, starting
   again at 1.
4. Push the branch, then send the push notification: the user should
   re-review the PR next.
5. Leave the impl note open; don't reseal or reopen anything else.

## Stop and ask if

- `vault.py preflight` fails, or passes with a warning that means the plan
  now rests on something no longer true.
- Any "Stop and ask if" line in the plan applies, or a step turns out to
  rest on something that's no longer true.
- The reviewer can't be run on a stronger model (see `reviewer.md`) — don't
  silently fall back to reviewing on the implementer's own model.
- A step's `Files:`/`Do:` doesn't match what the repo actually needs, beyond
  a small, worth-recording deviation.
- The third review still has a major finding.
- A PR-review request conflicts with the plan or the acceptance criteria.
`````

### `skills/implement-story/reviewer.md` (whole file)
`````markdown
# implement-story reviewer

The prompt `implement-story` sends to an independent reviewer subagent over
the whole diff once all steps are committed (again after each round of fixes,
up to three reviews), and once over all the fixes made while addressing PR
review. The reviewer runs on a stronger model than the implementer (Opus),
because judgment is where the stronger model pays off; the implementer's own
session model is never switched.

## Requesting it

Launch it as an agent with `model: "opus"` — an explicit override, never the
implementer's own model. If the tool available in this session has no way to
choose a subagent's model, stop and ask the user instead of reviewing on the
implementer's own model (`implement-story`'s own "Stop and ask if").

## Prompt

Give the reviewer, verbatim and in full:

1. What's under review: the whole diff, against every acceptance criterion
   below, not just the last commit. For PR-review fixes, also the request
   texts (the comments or review) the fixes answer, and which commit to look
   at first if one fix is large.
2. The plan's `## Acceptance criteria`.
3. The repo's `CLAUDE.md`, in full.
4. The diff: `git diff <base>..HEAD` for the end review, `git diff
   <tip>..HEAD` (the branch tip before the first fix) for PR-review fixes.
5. The exact instructions under "Findings format" below — the severity
   definitions, the reporting limit and the requirement to answer with
   nothing but those lines.

Ask it to check the diff against all of the above: does it do what was
asked, does it hold every acceptance criterion it touches, does it follow
the repo's own conventions — and nothing else. No style opinions the repo
`CLAUDE.md` doesn't already state, and no re-reviewing anything outside this
diff.

## Findings format

The reviewer answers with one line per finding, most important first:

```
(major) <one-sentence finding, self-contained>
(minor) <one-sentence finding, self-contained>
```

or `No findings.` if there's nothing to report. Nothing else in the reply.

It reports every major finding and at most three minor ones, most important
first; it drops the rest.

`major`: the diff doesn't do what it claims, breaks an acceptance criterion
it touches, or contradicts the repo `CLAUDE.md`. `minor`: everything else
worth recording — a missed edge case, a simplification, a small
inconsistency.

The reviewer doesn't number or resolve its own findings, doesn't tag them,
and doesn't know the eventual fix's commit sha — `implement-story` adds all
of that, continuing the numbering already in the impl note's
`## Review findings`:

```
- R<n> (major|minor): Review <k>: <finding> → fixed in <sha>
- R<n> (major|minor): Review <k>: <finding> → won't fix: <reason>
- R<n> (minor): Review <k>: <finding> → left open
- R<n> (major|minor): PR review: <finding> → fixed in <sha>
```

A review that comes back `No findings.` is recorded as a `## Verification`
line (`- Review <k>: no findings`), not under `## Review findings`; the
`Review <k>:` lines are also how a later session counts the rounds already
used.
`````

### `tests/test_skills.py`: `read`, `AssertMentionsMixin`, `ImplementStoryProcedureTest`, `ApprovalsSectionTest`
`````python
def read(skill: str, name: str) -> str:
    return (SKILLS_DIR / skill / name).read_text(encoding="utf-8")


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


class ApprovalsSectionTest(unittest.TestCase):  # T1, T2 -> AC1-AC4 (#74)
    HEADING = "Approvals, pings and the closing summary"

    @staticmethod
    def collapse(text):
        return " ".join(text.split())

    def section(self, skill, heading):
        text = read(skill, "SKILL.md")
        m = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.S | re.M)
        self.assertIsNotNone(m, f"{skill} has no '## {heading}' section")
        return m.group(1)

    def step(self, section, n):
        m = re.search(rf"^{n}\. (.*?)(?=^\d+\. |\Z)", section, re.S | re.M)
        self.assertIsNotNone(m, f"no step {n}")
        return m.group(1)

    def item(self, section, prefix, indent=""):
        m = re.search(rf"^{indent}- ({re.escape(prefix)}.*?)(?=^{indent}- |\Z)", section, re.S | re.M)
        self.assertIsNotNone(m, f"no item starting {prefix!r}")
        return m.group(1)

    def test_section_identical_and_right_after_never(self):               # T1
        sections = {s: self.collapse(self.section(s, self.HEADING)) for s in SKILLS}
        self.assertEqual(len(set(sections.values())), 1, "the five copies differ")
        for skill in SKILLS:
            headings = re.findall(r"^## (.*)$", read(skill, "SKILL.md"), re.M)
            self.assertEqual(headings[headings.index("Never") + 1], self.HEADING, skill)

    def test_section_says_the_three_rules(self):                          # T1
        low = self.collapse(self.section("issue", self.HEADING)).lower()
        for phrase in ("askuserquestion", "`yes`", "`no`", "`hold on`", "only `yes` proceeds",
                       "ask in prose why", "starts with a clear yes", "asks for a change",
                       "counts as `hold on`", "open-ended prose", "`pushnotification`",
                       "under 200 characters", "at most three bullets", "nothing after them"):
            self.assertIn(phrase, low)

    def test_every_approval_step_uses_the_prompt(self):                   # T2
        need = "the approval prompt"
        checks = {
            "plan-story": [("Approval", None), ("Issue hygiene", "- Post GitHub edits")],
            "design": [("Approval", 1), ("File the stories", 4)],
            "retro": [("Follow-ups", 1), ("Follow-ups", 2), ("Decisions", None), ("Seal", 2),
                      ("Stop and ask if", "- `seal retro` prints a feature-close"),
                      ("Stop and ask if", "- `seal retro` refuses because the PR")],
            "issue": [("Approval", None), ("Create", 3)],
        }
        for skill, items in checks.items():
            for heading, which in items:
                body = self.section(skill, heading)
                if isinstance(which, int):
                    body = self.step(body, which)
                elif which:
                    body = self.item(body, which[2:])
                self.assertIn(need, self.collapse(body), f"{skill}: {heading} {which}")
        follow = self.step(self.section("retro", "Follow-ups"), 1)
        for prefix in ("`- issue #<n> created`", "`- issue #<n> amended`"):
            self.assertIn(need, self.collapse(self.item(follow, prefix, "   ")), prefix)
        for skill in ("design", "plan-story", "retro", "issue"):
            self.assertIn(need, self.collapse(self.section(skill, self.HEADING)))

    def test_implement_story_asks_no_approval(self):                      # T2
        text = read("implement-story", "SKILL.md")
        shared = self.section("implement-story", self.HEADING)
        self.assertNotIn("the approval prompt", text.replace(shared, ""))

    def sentences(self, text):
        return re.split(r"(?<=\.)\s+", self.collapse(text))

    def test_ping_points_name_the_next_stage(self):                       # T3
        points = [
            ("plan-story", "Handoff", None, "implement #<issue>"),
            ("design", "File the stories", 5, "plan"),
            ("design", "Filing later", None, "plan"),
            ("implement-story", "Finish", 6, "retro #<issue>"),
            ("implement-story", "No-PR plan", 4, "retro #<issue>"),
            ("implement-story", "Address PR review", 4, "re-review"),
            ("retro", "Seal", 3, "next issue"),
            ("issue", "Create", 4, "plan"),
        ]
        for skill, heading, n, next_stage in points:
            body = self.section(skill, heading)
            if n:
                body = self.step(body, n)
            hits = [s for s in self.sentences(body) if "push notification" in s]
            self.assertTrue(hits, f"{skill}: {heading} has no push notification")
            self.assertTrue(any(next_stage in s.lower() for s in hits), f"{skill}: {heading} {hits}")

    def test_repo_claude_md_mentions_the_section(self):                   # T4
        text = (REPO / "CLAUDE.md").read_text(encoding="utf-8")
        bullet = re.search(r"^- `skills/`:.*$", text, re.M).group(0)
        self.assertIn("## Approvals, pings and the closing summary", bullet)
`````

### `agent_vault/claudemd.py`: `check_commands`
`````python
def check_commands(repo: Path, sections: list, lines: list) -> list:
    errors = []
    commands_secs = [s for s in sections if s.name == "Commands"]
    if not commands_secs:
        return errors
    seen = set()
    for sec in commands_secs:
        for lineno in range(sec.start, sec.end + 1):
            if lineno > len(lines):
                break
            for tok in BACKTICK_RE.findall(lines[lineno - 1]):
                tok = tok.strip()
                if not tok or tok in seen:
                    continue
                seen.add(tok)
                first = tok.split()[0] if tok.split() else ""
                if not first or "<" in first or ">" in first:
                    continue
                if not resolve_command(repo, tok):
                    errors.append(f"line {lineno}: command '{tok}' does not resolve")
    return errors
`````

