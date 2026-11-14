base_sha: 6377896

# Context

Excerpts at the plan's base commit.

### `agent_vault/vault.py`: module docstring
`````python
"""The one way to create, move, and check notes in Agent/.

Usage: <CLI> <command>, where <CLI> is the exact command in the vault's
CLAUDE.md (the installer's Python plus this file). Commands:
  new project <id> [--repo owner/name ...] [--purpose "..."]
  new plan <repo> <issue>
  new retro <repo> <issue>
  new design <project> "<title>"
  seal plan <repo> <issue>
  seal retro <repo> <issue>
  seal design <design>
  design stories <design>
  features [--project <id>]
  preflight <repo> <issue>
  branch <repo> <issue> [--type feat] [--slug <slug>]
  open-pr <repo> <issue> [--body-file <file>]
  stage <repo> <issue>
  idea add "<title>" [--project <id>] [--source <source>]
  idea drop <file> --reason "..."
  idea promote <file> --body-file <file> [--title "..."] [--repo <name>]
      (--parent <n>|<repo>#<n> | --standalone | --bug)
  issue create <repo> --title "..." --body-file <file> [--idea <file>]
      (--feature <design> | --parent <n>|<repo>#<n> | --standalone | --bug)
  decision add <project> "<title>" --decision "..." --why "..." --source "..." [--replaces <record>]
  decisions [--project <id>] [--all]
  ideas [--project <id>] [--status open|promoted|dropped]
  ideas review
  handoff list
  handoff pull "<phone path>" [--project <id>]
  log "what happened" [--project <id>]
  repo-init <path>
  claudemd-lint [<repo path>]
  lint
  validate
  status
  schema
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
`````

### `agent_vault/vault.py`: `BRANCH_SLUG_MAX`
`````python
BRANCH_SLUG_MAX = 40        # default branch slug length
`````

### `agent_vault/vault.py`: `check_preflight`, `require_clone`, `default_slug`, `cmd_branch`, `build_parser`
`````python
def check_preflight(repo: str, issue: str, slug: str) -> tuple[dict, list[str]]:
    """Dies unless the plan is sealed, the issue is open and its title and body are unchanged
    since sealing. Returns (issue info, warnings)."""
    path, fm, body = load_plan(repo, issue)
    if fm.get("status") != "sealed":
        die(f"{lib.rel(path)} is not sealed; seal it with `seal plan {repo} {issue}`")
    info = github.issue_info(slug, issue)
    if info["state"] != "OPEN":
        die(f"issue #{issue} is {info['state'].lower()}")
    if info["edited"] > fm["issue_updated"]:
        die(f"issue #{issue}'s title or body was edited at {info['edited']}, after the plan was "
            f"sealed ({fm['issue_updated']}); re-plan it (plan-story) before implementing")
    try:
        cmp = github.compare(slug, fm["base_sha"], github.default_branch(slug))
    except github.CmdError as e:
        if e.kind != "failed":
            raise
        return info, [f"could not compare base_sha {fm['base_sha']} with the default branch: {e}"]
    named = plan_files(body)
    touched = sorted({f["filename"] for f in cmp.get("files", [])
                      if any(f["filename"] == n or f["filename"].endswith("/" + n) for n in named)})
    if not touched:
        return info, []
    return info, [f"{cmp.get('total_commits', '?')} commit(s) since base_sha {fm['base_sha']} "
                  f"touch files the plan names: {', '.join(touched)}"]


def require_clone(slug: str) -> Path:
    """The git clone we're running in, checked to be a clone of `slug` (D5)."""
    top = github.git("rev-parse", "--show-toplevel", check=False)
    if not top:
        die("this command runs inside a clone of the repo; cd into it first")
    origin = github.origin_slug(top)
    if origin.lower() != slug.lower():
        die(f"this clone's origin is {origin}, not {slug}; run from a clone of {slug}")
    return Path(top)


def default_slug(title: str) -> str:
    subject = re.sub(r"^\s*[A-Za-z]+(?:\([^)]*\))?!?:\s*", "", title)
    return lib.slugify(subject)[:BRANCH_SLUG_MAX].strip("-") or "work"


def cmd_branch(args) -> None:
    slug = issue_context(args)
    refuse_no_pr(args.repo, args.issue, "there is nothing to branch")
    info, warnings = check_preflight(args.repo, args.issue, slug)
    for w in warnings:
        print(f"warning: {w}")
    impl, _ = load_impl(args.repo, args.issue)
    top = require_clone(slug)
    name = f"{args.type}/{args.issue}-{args.slug or default_slug(info['title'])}"
    if not lib.BRANCH_RE.match(name):
        die(f"branch '{name}' must match <type>/<issue>-<slug> with a lowercase a-z0-9- slug")
    pat = issue_branch_re(args.issue)
    local = github.git("for-each-ref", "--format=%(refname:short)", "refs/heads", cwd=top).splitlines()
    found = [f"{b} (local)" for b in local if pat.match(b)]
    found += [f"{b} (on GitHub)" for b in github.remote_branches(slug) if pat.match(b)]
    if found:
        die(f"issue #{args.issue} already has a branch: {', '.join(found)}")
    base = github.default_branch(slug)
    github.git("fetch", "origin", base, cwd=top)
    github.git("switch", "--no-track", "-c", name, f"origin/{base}", cwd=top)
    github.git("push", "-u", "origin", name, cwd=top)
    update_note(impl, branch=name)
    log_event(args.repo, args.issue, "started", name)
    print(f"created and pushed {name} from {base}; recorded in {lib.rel(impl)}")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="vault.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="<command>")

    new = sub.add_parser("new", help="create a project, plan or retro note").add_subparsers(
        dest="kind", required=True, metavar="project|plan|retro|design")
    p = new.add_parser("project")
    p.add_argument("id")
    p.add_argument("--repo", action="append", default=[], metavar="OWNER/NAME")
    p.add_argument("--purpose")
    p.set_defaults(fn=cmd_new_project)
    for kind, fn in (("plan", cmd_new_plan), ("retro", cmd_new_retro)):
        p = new.add_parser(kind)
        p.add_argument("repo")
        p.add_argument("issue")
        p.set_defaults(fn=fn)
    p = new.add_parser("design", help="a draft design for a project (which need not exist yet)")
    p.add_argument("project")
    p.add_argument("title")
    p.set_defaults(fn=cmd_new_design)

    seal = sub.add_parser("seal", help="seal a plan, a retro or a design").add_subparsers(
        dest="kind", required=True, metavar="plan|retro|design")
    for kind, fn in (("plan", cmd_seal_plan), ("retro", cmd_seal_retro)):
        p = seal.add_parser(kind)
        p.add_argument("repo")
        p.add_argument("issue")
        p.set_defaults(fn=fn)
    p = seal.add_parser("design", help="record its decisions, promote its ideas, then seal it")
    p.add_argument("design")
    p.set_defaults(fn=cmd_seal_design)
    for name, fn in (("preflight", cmd_preflight), ("stage", cmd_stage), ("branch", cmd_branch),
                     ("open-pr", cmd_open_pr)):
        p = sub.add_parser(name)
        p.add_argument("repo")
        p.add_argument("issue")
        if name == "branch":
            p.add_argument("--type", default="feat", choices=lib.BRANCH_TYPES)
            p.add_argument("--slug")
        if name == "open-pr":
            p.add_argument("--body-file")
        p.set_defaults(fn=fn)
    p = sub.add_parser("repo-init", help="set up a repo's merge settings, label, hooks, CLAUDE.md and issue template")
    p.add_argument("path")
    p.set_defaults(fn=cmd_repo_init)
    p = sub.add_parser("claudemd-lint", help="check a repo's CLAUDE.md (default: the current clone)")
    p.add_argument("path", nargs="?")
    p.set_defaults(fn=cmd_claudemd_lint)
    p = sub.add_parser("lint", help="claudemd-lint plus the repo's declared lint command (current clone)")
    p.set_defaults(fn=cmd_lint)

    idea = sub.add_parser("idea", help="add, drop or promote an idea").add_subparsers(
        dest="action", required=True, metavar="add|drop|promote")
    p = idea.add_parser("add")
    p.add_argument("title")
    p.add_argument("--project")
    p.add_argument("--source", default="session")
    p.set_defaults(fn=cmd_idea_add)
    p = idea.add_parser("drop")
    p.add_argument("file")
    p.add_argument("--reason", required=True)
    p.set_defaults(fn=cmd_idea_drop)
    p = idea.add_parser("promote")
    p.add_argument("file")
    p.add_argument("--title")
    p.add_argument("--repo")
    where = p.add_mutually_exclusive_group(required=True)
    where.add_argument("--parent", metavar="N|REPO#N", help="file the issue under this open feature")
    where.add_argument("--standalone", action="store_true", help="file the issue under no feature, with no label")
    where.add_argument("--bug", action="store_true", help="file a standalone issue labelled 'bug'")
    p.add_argument("--body-file", required=True, help="the issue body, in the issue template's sections")
    p.set_defaults(fn=cmd_idea_promote)

    issue = sub.add_parser("issue", help="create an issue from a templated body").add_subparsers(
        dest="action", required=True, metavar="create")
    p = issue.add_parser("create", help="refuses a body that doesn't match the issue template")
    p.add_argument("repo")
    p.add_argument("--title", required=True)
    p.add_argument("--body-file", required=True)
    p.add_argument("--idea", help="an open idea to mark promoted")
    where = p.add_mutually_exclusive_group(required=True)
    where.add_argument("--feature", metavar="DESIGN", help="file the sealed design's feature issue")
    where.add_argument("--parent", metavar="N|REPO#N", help="file the issue under this open feature")
    where.add_argument("--standalone", action="store_true", help="file the issue under no feature, with no label")
    where.add_argument("--bug", action="store_true", help="file a standalone issue labelled 'bug'")
    p.set_defaults(fn=cmd_issue_create)

    p = sub.add_parser("ideas", help="list ideas, or `ideas review` for old open ones")
    p.add_argument("action", nargs="?", choices=["review"])
    p.add_argument("--project")
    p.add_argument("--status", default="open", choices=lib.STATUSES["idea"])
    p.set_defaults(fn=cmd_ideas)

    dec = sub.add_parser("decision", help="record a decision").add_subparsers(
        dest="action", required=True, metavar="add")
    p = dec.add_parser("add", help="create the project's next decision record (never edited afterwards)")
    p.add_argument("project")
    p.add_argument("title")
    p.add_argument("--decision", required=True)
    p.add_argument("--why", required=True)
    p.add_argument("--source", required=True,
                   help="plan <repo>#<n> D<k> | design <design> D<k> | issue <repo>#<n> | retro <repo>#<n> | session")
    p.add_argument("--replaces", help="the active record this one reverses; it is marked superseded")
    p.set_defaults(fn=cmd_decision_add)
    des = sub.add_parser("design", help="list a sealed design's stories with their issues").add_subparsers(
        dest="action", required=True, metavar="stories")
    p = des.add_parser("stories", help="each story with the issues whose Source names it, or 'not filed'")
    p.add_argument("design")
    p.set_defaults(fn=cmd_design_stories)
    p = sub.add_parser("features", help="the open feature issues, with how many sub-issues are open")
    p.add_argument("--project")
    p.set_defaults(fn=cmd_features)
    p = sub.add_parser("decisions", help="list active decision records (--all: superseded too)")
    p.add_argument("--project")
    p.add_argument("--all", action="store_true")
    p.set_defaults(fn=cmd_decisions)

    p = sub.add_parser("log")
    p.add_argument("text")
    p.add_argument("--project")
    p.set_defaults(fn=cmd_log)

    hand = sub.add_parser("handoff", help="list or pull phone-side notes").add_subparsers(
        dest="action", required=True, metavar="list|pull")
    hand.add_parser("list").set_defaults(fn=cmd_handoff_list)
    p = hand.add_parser("pull")
    p.add_argument("path")
    p.add_argument("--project")
    p.set_defaults(fn=cmd_handoff_pull)

    for name, fn in (("validate", cmd_validate), ("status", cmd_status), ("schema", cmd_schema)):
        sub.add_parser(name).set_defaults(fn=fn)
    return ap
`````

### `agent_vault/github.py`: `issue_info`
`````python
def issue_info(slug: str, number: str) -> dict:
    """state, title, url, labels, `parent` ({number, url} or None), `sub_total`, `sub_completed` and `edited`:
    the last title or body edit (D1), never `updatedAt`, which comments, labels and cross-references also bump."""
    owner, name = slug.split("/")
    data = gh_json("api", "graphql", "-f", f"query={ISSUE_QUERY}", "-F", f"owner={owner}",
                   "-F", f"name={name}", "-F", f"number={number}")
    issue = ((data.get("data") or {}).get("repository") or {}).get("issue")
    if not issue:
        raise CmdError(f"issue #{number} was not found in {slug}")
    stamps = [issue["createdAt"], issue.get("lastEditedAt")]
    stamps += [n["createdAt"] for n in issue["timelineItems"]["nodes"] if n]
    return {"state": issue["state"], "title": issue["title"], "url": issue["url"],
            "labels": [n["name"] for n in issue["labels"]["nodes"]],
            "parent": issue.get("parent"),
            "sub_total": (issue.get("subIssuesSummary") or {}).get("total", 0),
            "sub_completed": (issue.get("subIssuesSummary") or {}).get("completed", 0),
            "edited": max(s for s in stamps if s)}
`````

### `agent_vault/lib.py`: branch types and the bug label
`````python
DESIGN_SOFT = ["DESIGN-DEC", "DESIGN-STORY", "DESIGN-IDEA"]         # the same, for a draft design
BRANCH_TYPES = ["feat", "fix", "chore", "docs", "refactor", "test", "perf", "hotfix"]   # design §4
BRANCH_RE = re.compile(rf"^(?:{'|'.join(BRANCH_TYPES)})/[0-9]+-[a-z0-9-]+$")
PLANNED_LABEL = ("planned", "0e8a16", "A sealed plan exists (set by agent-vault)")   # name, color, description
FEATURE_LABEL = ("feature", "5319e7", "A design's feature issue; its stories are sub-issues (set by agent-vault)")
BUG_LABEL = ("bug", "d73a4a", "Something isn't working")          # GitHub's own default colour and description
`````

### `agent_vault/githooks/pre-push`: the branch-name pattern
`````sh
re='^(feat|fix|chore|docs|refactor|test|perf|hotfix)/[0-9]+-[a-z0-9-]+$'
`````

### `tests/test_github.py`: `GhCase`, `BranchTest`
`````python
class GhCase(unittest.TestCase):
    """A vault with project `widgets` (repo acme/widget), a clone whose origin looks like the
    GitHub URL but really points at a local bare remote, and a fake gh."""

    def setUp(self):
        self._t = tmpdir()
        self.root = Path(self._t.name)
        copy_scenario(scenario().root, self.root)
        self.vault = self.root / "Notes"
        self.cfg = self.root / "agent-vault.json"
        self.state_path = self.root / "gh.json"
        self.bare = self.root / "remote.git"
        self.clone = self.root / "clone"
        patch = mock.patch.dict(os.environ, scenario_env(self.root))
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self._t.cleanup)
        self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self._pristine = self.fingerprint()

    def fingerprint(self):
        """Names, sizes and times of every file in the test's folder except git's object stores."""
        out = []
        for path in sorted(self.root.rglob("*")):
            parts = path.relative_to(self.root).parts
            if path.is_file() and "objects" not in parts:
                st = path.stat()
                out.append((parts, st.st_size, st.st_mtime_ns))
        return out

    def snapshot(self, key, build):
        """Run `build()`, or, when nothing has changed this test's folder since setUp or the last
        snapshot, copy the folder as an earlier `build()` left it, saved under `key`."""
        saved = _snapshots.get(key)
        clean = self.fingerprint() == self._pristine
        if saved is not None and clean:
            for child in self.root.iterdir():
                shutil.rmtree(child, onerror=_unlock) if child.is_dir() else child.unlink()
            copy_scenario(saved.root, self.root)
        else:
            build()
            if saved is None and clean:
                _snapshots[key] = saved = _Snapshot(self.root)
        self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self._pristine = self.fingerprint()

    # --- helpers ---
    def save(self):
        self.state_path.write_text(json.dumps(self.state), encoding="utf-8")

    def load(self):
        self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
        return self.state["repos"][SLUG]

    def issue(self, n="7"):
        return self.state["repos"][SLUG]["issues"][n]

    def cli(self, *args, cwd=None, env=None):
        return run_py(VAULT_PY, args, config=self.cfg, cwd=cwd or self.clone, env=env)

    def ok(self, r):
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def refused(self, r, *words):
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        for w in words:
            self.assertIn(w.lower(), (r.stderr + r.stdout).lower())
        return r

    def calls(self, *prefix):
        self.load()
        return [c for c in self.state["calls"] if c[:len(prefix)] == list(prefix)]

    def note(self, name):
        return self.vault / "Agent" / "Work" / "widget" / f"widget-7-{name}.md"

    def fm(self, path):
        text = path.read_text(encoding="utf-8")
        return dict(ln.split(": ", 1) for ln in text.split("---")[1].strip().splitlines())

    def daily(self):
        p = self.vault / "Agent" / "Daily" / f"{dt.date.today().isoformat()}.md"
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def assert_valid(self):
        r = self.cli("validate")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn(": error:", r.stdout)

    def idea(self, *args):
        r = self.ok(self.cli("idea", "add", "Frobnicate the gadget", *args))
        return r.stdout.split()[-1].split("/")[-1]

    def body_file(self, text=ISSUE_BODY, name="body.md", raw=None):
        path = self.root / name
        if raw is not None:
            path.write_bytes(raw)
        else:
            path.write_text(text, encoding="utf-8", newline="\n")
        return str(path)

    def new_plan(self, text=SEALED_PLAN):
        self.ok(self.cli("new", "plan", "widget", "7"))
        self.note("plan").write_text(text, encoding="utf-8", newline="\n")

    def sealed(self, plan=SEALED_PLAN):
        self.new_plan(plan)
        self.ok(self.cli("seal", "plan", "widget", "7"))
        self.load()

    def branch(self, *extra):
        return self.cli("branch", "widget", "7", *extra)


class BranchTest(GhCase):  # T3 -> AC3, AC11
    def test_creates_pushes_and_records(self):
        self.sealed()
        self.ok(self.branch())
        name = "feat/7-add-widget-frobbing"                       # the `feat:` prefix is dropped
        self.assertEqual(git("branch", "--show-current", cwd=self.clone), name)
        self.assertIn(name, git("ls-remote", "--heads", "origin", cwd=self.clone))
        self.assertEqual(git("rev-parse", "--abbrev-ref", "@{u}", cwd=self.clone), f"origin/{name}")
        self.assertEqual(self.fm(self.note("impl"))["branch"], name)
        self.assertRegex(self.daily(), rf"- \d\d:\d\d widget#7 started — {name}\n")
        self.assert_valid()

    def test_type_and_slug_options(self):
        self.sealed()
        self.ok(self.branch("--type", "fix", "--slug", "frob-fix"))
        self.assertEqual(git("branch", "--show-current", cwd=self.clone), "fix/7-frob-fix")

    def test_refuses_existing_local_branch(self):
        self.sealed()
        git("branch", "chore/7-old", cwd=self.clone)
        self.refused(self.branch(), "already has a branch", "chore/7-old")
        self.assertEqual(git("branch", "--show-current", cwd=self.clone), "main")

    def test_refuses_existing_remote_branch(self):
        self.sealed()
        self.state["repos"][SLUG]["branches"] = ["fix/7-elsewhere"]
        self.save()
        self.refused(self.branch(), "already has a branch", "on GitHub")

    def test_refuses_bad_slug(self):
        self.sealed()
        self.refused(self.branch("--slug", "Bad Slug"), "must match")

    def test_needs_preflight_and_the_right_clone(self):
        self.new_plan()
        self.refused(self.branch(), "not sealed")
        git("remote", "set-url", "origin", "https://github.com/acme/other.git", cwd=self.clone)
        self.ok(self.cli("seal", "plan", "widget", "7"))
        self.refused(self.branch(), "acme/other", SLUG)
`````

### `skills/implement-story/SKILL.md`: `## Start`
`````markdown
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
     `vault.py branch <repo> <issue> [--type <type>] [--slug <slug>]`
     (`--type` if the plan's steps clearly call for something other than
     `feat`, `--slug` to override the default slug). This checks out the new
     branch and records it in the impl note.
   - Found: fetch and check it out (`git switch <branch>`, or
     `git switch -c <branch> origin/<branch>` if it isn't local yet). If the
     impl note's `branch:` field is still empty (e.g. the branch was made by
     hand), record it there now, directly — `open-pr` needs it later and
     nothing else will set it. Then go to **Resume**.
`````

### `templates/vault-CLAUDE.md`: the line naming `branch`
`````markdown
- `{{CLI}} seal plan <repo> <issue>`: after I approve a plan; records the issue's edit time and the base commit, adds the `planned` label and logs it. `{{CLI}} preflight <repo> <issue>` gates implementation; `{{CLI}} branch <repo> <issue> [--type feat] [--slug ...]` creates and pushes the branch; `{{CLI}} open-pr <repo> <issue> [--body-file <file>]` opens the PR with `Closes #<issue>`; `{{CLI}} stage <repo> <issue>` says where the issue stands. `branch` and `open-pr` run inside a clone of the repo.
`````

### `tests/test_skills.py`: `VaultCommandsTest`
`````python
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
`````

### Design: `agent-vault-design-2-features-group-issues-as-github-sub-issues` (the `branch` bullet of Chosen design, D4, S3)
`````markdown
- `vault.py branch`: a `bug`-labelled issue gets `fix/`, and `--type` other than `fix` is refused for it; `--type fix` or `hotfix` is refused for any other issue, whose default stays `feat`.

### D4. A bug is always standalone, labelled bug, and branched fix/
Decision: `issue create --bug` files a standalone issue with the `bug` label; `vault.py branch` gives a bug `fix/` and refuses `fix`/`hotfix` for any other issue.
Why: The user wants bugs and feature work told apart at a glance, decided once at filing; git refuses `:` in branch names, so the existing branch type carries the marker.

### S3. Bugs branch as fix/
Outcome: `vault.py branch` gives a `bug`-labelled issue a `fix/` branch and refuses another type for it, refuses `fix` and `hotfix` for an issue without the label, and still defaults other issues to `feat/`; implement-story's wording about choosing `--type` matches.
Depends on: S2
`````

### Files under `agent_vault/` at the base commit
`````
agent_vault/claudemd.py
agent_vault/githooks/pre-push
agent_vault/github.py
agent_vault/hooks/guard.py
agent_vault/hooks/post_edit.py
agent_vault/hooks/push_guard.py
agent_vault/hooks/session_start.py
agent_vault/hooks/stop_check.py
agent_vault/lib.py
agent_vault/templates/repo-CLAUDE.md
agent_vault/vault.py
`````

