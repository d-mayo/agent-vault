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
  closes-feature <repo> <issue>
  preflight <repo> <issue>
  branch <repo> <issue> [--type <type>] [--slug <slug>]   (type: fix for a bug-labelled issue, else feat)
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
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True  # keep __pycache__ out of iCloud
import claudemd  # noqa: E402
import github  # noqa: E402
import lib  # noqa: E402
from lib import AGENT, FOLDERS, VAULT  # noqa: E402

REVIEW_DAYS = 90            # `ideas review` lists open ideas older than this
SLUG_MAX = 60
BRANCH_SLUG_MAX = 40        # default branch slug length


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def one_line(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def require_project(index: lib.Index, pid: str | None) -> None:
    if pid and pid not in index.projects:
        known = ", ".join(sorted(index.projects)) or "(none yet)"
        die(f"project '{pid}' does not exist. Known projects: {known}")


def require_repo(index: lib.Index, repo: str) -> None:
    if repo not in index.repos:
        known = ", ".join(sorted(index.repos)) or "(none yet)"
        die(f"repo '{repo}' is not listed in any project's repos:. Known repos: {known}")


def require_number(value: str, what: str = "issue") -> str:
    if not lib.NUMBER_RE.match(value):
        die(f"{what} must be a number like 12 (got {value!r})")
    return value


def commit_note(path: Path, text: str) -> None:
    """Write a note, then validate it. If that adds errors the note didn't already
    have, put the file back as it was and refuse, so no command leaves a bad note."""
    old = lib.read_text(path) if path.exists() else None
    before = set(lib.validate_file(path).errors) if old is not None else set()
    lib.write_text(path, text)
    new = [e for e in lib.validate_file(path).errors if e not in before]
    if new:
        if old is None:
            path.unlink()
        else:
            lib.write_text(path, old)
        die(f"refused: this would leave {lib.rel(path)} invalid:\n  " + "\n  ".join(new))


# --- note templates ------------------------------------------------------------
def project_note(pid: str, repos: list[str], purpose: str) -> str:
    fm = lib.render_frontmatter({"type": "project", "id": pid, "status": "active", "repos": repos})
    return (f"{fm}# {pid}\n\n"
            f"## Purpose\n{purpose or '(not written yet)'}\n\n"
            "## Current state\n(nothing yet)\n\n"
            "## Architecture\n(nothing yet)\n\n"
            f"## {lib.STANDING}\n{lib.NONE_LINE}\n")


def plan_note(repo: str, issue: str) -> str:
    fm = lib.render_frontmatter({"type": "plan", "repo": repo, "issue": issue, "status": "draft"})
    return (f"{fm}# {repo}-{issue}-plan\n\n"
            "## Goal\n<!-- What this issue achieves and why. -->\n\n"
            "## Acceptance criteria\n\n"
            "## Decisions\n\n"
            "## Implementer's discretion\n<!-- What the implementer may decide alone. -->\n\n"
            "## Context\n<!-- Files, functions and specs the implementer needs. -->\n\n"
            "## Steps\n<!-- One '### <n>. <title>' per step, each with Files:, Do: and Done when: lines. -->\n\n"
            "## Tests\n\n"
            "## Stop and ask if\n<!-- Conditions that need the user's decision. -->\n\n"
            "## Out of scope\n<!-- What this issue must not touch. -->\n")


def impl_note(repo: str, issue: str) -> str:
    fm = lib.render_frontmatter({"type": "impl", "repo": repo, "issue": issue, "status": "open"})
    return (f"{fm}# {repo}-{issue}-impl\n\n"
            "## Deviations\nNone\n\n"
            "## Review findings\nNone\n\n"
            "## Verification\n<!-- What was run and what it showed. -->\n\n"
            "## Discoveries\nNone\n")


def retro_note(repo: str, issue: str, pr: str) -> str:
    """A fresh retro; a `pr: none` one also gets the closing comment `seal retro` posts."""
    fm = lib.render_frontmatter({"type": "retro", "repo": repo, "issue": issue, "pr": pr, "status": "open"})
    return (f"{fm}# {repo}-{issue}-retro\n\n"
            "## Summary\n<!-- What was built and how it went. -->\n\n"
            "## CLAUDE.md audit\n\n"
            "## README audit\n\n"
            "## Overview audit\n\n"
            "## Follow-ups\nNone\n"
            + (f"\n## {lib.CLOSING}\n<!-- The comment the issue is closed with; the user approves it with this retro. -->\n"
               if pr == lib.NO_PR else ""))


def idea_note(title: str, source: str, project: str | None, context: str = "") -> str:
    fm = {"type": "idea"}
    if project:
        fm["project"] = project
    fm.update({"source": source, "status": "open", "created": lib.today().isoformat()})
    return f"{lib.render_frontmatter(fm)}# {title}\n" + (context + "\n" if context else "")


def work_path(repo: str, issue: str, kind: str) -> Path:
    return AGENT / FOLDERS["work"] / repo / f"{repo}-{issue}-{kind}.md"


# --- daily note and log lines ------------------------------------------------------
def create_daily(d: dt.date | None = None) -> tuple[Path, bool]:
    d = d or lib.today()
    path = lib.daily_path(d)
    if path.exists():
        return path, False
    fm = lib.render_frontmatter({"type": "daily", "date": d.isoformat()})
    title = f"{d.strftime('%A, %B')} {d.day}, {d.year}"
    lib.write_text(path, f"{fm}# {title}\n\n{lib.LOG_HEADING}\n")
    return path, True


def append_log(entry: str) -> Path:
    """Add one line under today's `## Log`, creating the daily note if needed."""
    path, _ = create_daily()
    commit_note(path, lib.read_text(path).rstrip("\n") + "\n" + entry + "\n")
    return path


def log_idea_event(event: str, title: str, idea: Path) -> None:
    append_log(f"- {lib.now_hhmm()} {event} — {title} [[{idea.stem}]]")


# --- new ---------------------------------------------------------------------
def cmd_new_project(args) -> None:
    index = lib.Index()
    pid = args.id
    if not lib.ID_RE.match(pid):
        die("project id must be lowercase-kebab-case, e.g. garden-redesign")
    path = AGENT / FOLDERS["project"] / f"{pid}.md"
    if pid in index.projects or path.exists():
        die(f"project '{pid}' already exists: {lib.rel(path)}")
    names = []
    for slug in args.repo:
        if not lib.REPO_SLUG_RE.match(slug):
            die(f"repo '{slug}' must look like owner/name")
        name = slug.split("/")[-1]
        if name in names:
            die(f"repo name '{name}' is given twice")
        if name in index.repos:
            die(f"repo name '{name}' is already listed by project '{index.repos[name][0]}'")
        names.append(name)
    commit_note(path, project_note(pid, args.repo, one_line(args.purpose or "")))
    print("created " + lib.rel(path))


def cmd_new_plan(args) -> None:
    index = lib.Index()
    require_number(args.issue)
    require_repo(index, args.repo)
    plan, impl = work_path(args.repo, args.issue, "plan"), work_path(args.repo, args.issue, "impl")
    for p in (plan, impl):
        if p.exists():
            die(f"{lib.rel(p)} already exists")
    commit_note(plan, plan_note(args.repo, args.issue))
    commit_note(impl, impl_note(args.repo, args.issue))
    print("created " + lib.rel(plan))
    print("created " + lib.rel(impl))


def cmd_new_retro(args) -> None:
    index = lib.Index()
    require_number(args.issue)
    require_repo(index, args.repo)
    impl, retro = work_path(args.repo, args.issue, "impl"), work_path(args.repo, args.issue, "retro")
    if not impl.is_file():
        die(f"{lib.rel(impl)} doesn't exist; a retro needs the impl note")
    if retro.exists():
        die(f"{lib.rel(retro)} already exists")
    fm, _, _ = lib.split_frontmatter(lib.read_text(impl))
    pr = (fm or {}).get("pr")
    if plan_is_no_pr(args.repo, args.issue):
        pr = lib.NO_PR
    elif not isinstance(pr, str) or not lib.NUMBER_RE.match(pr):
        die(f"{lib.rel(impl)} has no 'pr:' yet; open the PR first")
    commit_note(retro, retro_note(args.repo, args.issue, pr))
    print("created " + lib.rel(retro))


def design_note(project: str, title: str) -> str:
    fm = lib.render_frontmatter({"type": "design", "project": project, "status": "draft",
                                 "created": lib.today().isoformat()})
    return (f"{fm}# {title}\n\n"
            "## Problem\n<!-- What is wrong or missing, and for whom. -->\n\n"
            "## Goals\n<!-- What this design must achieve. -->\n\n"
            "## Non-goals\n<!-- What it deliberately does not cover. -->\n\n"
            "## Ideas\nNone\n\n"
            "## Options considered\n<!-- At least two genuinely different options for each major choice, "
            "each with its trade-offs. -->\n\n"
            "## Chosen design\n<!-- The design, the technology it needs, and Mermaid diagrams where they read "
            "better than prose. -->\n\n"
            "## Decisions\n\n"
            "## Stories\n\n"
            "## Open questions\n<!-- Risks and unknowns; never write these up as decisions. -->\n")


def cmd_new_design(args) -> None:
    project = args.project
    if not lib.ID_RE.match(project):
        die("project must be a lowercase-kebab-case project id, e.g. garden-redesign")
    if not one_line(args.title):
        die("the title is empty")
    title = lib.safe_title(one_line(args.title))
    folder = AGENT / FOLDERS["design"] / project
    taken = [parts[0] for p in folder.glob("*.md") if (parts := lib.design_parts(project, p.stem))]
    slug = lib.slugify(title)[:SLUG_MAX].strip("-") or "untitled"
    path = folder / f"{project}-design-{max(taken, default=0) + 1}-{slug}.md"
    commit_note(path, design_note(project, title))
    print("created " + lib.rel(path))


def set_fields(path: Path, **fields) -> None:
    """Update frontmatter fields in place. Only hooks/stop_check.py uses this (#5 owns that hook)."""
    fm, body, _ = lib.split_frontmatter(lib.read_text(path))
    fm = fm or {}
    fm.update(fields)
    lib.write_text(path, lib.render_frontmatter(fm) + body.lstrip("\n"))


# --- git and GitHub commands (design §4, §10) ------------------------------------------
REPO_SETTINGS = {"allow_squash_merge": True, "allow_merge_commit": False,
                 "allow_rebase_merge": False, "delete_branch_on_merge": True}
MANUAL_SETTINGS = ("on GitHub, Settings → General → Pull Requests: allow only 'Squash merging' "
                   "and turn on 'Automatically delete head branches'")


def update_note(path: Path, **fields) -> None:
    """Set frontmatter fields on a note, keeping its body; refuses if that would leave it invalid."""
    fm, body, _ = lib.split_frontmatter(lib.read_text(path))
    text = lib.render_frontmatter({**(fm or {}), **fields}) + body.lstrip("\n")
    commit_note(path, text if text.endswith("\n") else text + "\n")


def log_event(repo: str, issue: str, event: str, detail: str = "") -> None:
    append_log(f"- {lib.now_hhmm()} {repo}#{issue} {event}" + (f" — {one_line(detail)}" if detail else ""))


def load_plan(repo: str, issue: str) -> tuple[Path, dict, str]:
    path = work_path(repo, issue, "plan")
    if not path.is_file():
        die(f"{lib.rel(path)} doesn't exist; `new plan {repo} {issue}` creates it")
    fm, body, _ = lib.split_frontmatter(lib.read_text(path))
    return path, fm or {}, body


def plan_is_no_pr(repo: str, issue: str) -> bool:
    """True when the issue's plan declares `pr: none` (no plan: False)."""
    path = work_path(repo, issue, "plan")
    if not path.is_file():
        return False
    fm, _, _ = lib.split_frontmatter(lib.read_text(path))
    return (fm or {}).get("pr") == lib.NO_PR


def refuse_no_pr(repo: str, issue: str, what: str) -> None:
    if plan_is_no_pr(repo, issue):
        die(f"{repo}#{issue}'s plan declares pr: none, so it has no branch or pull request; {what}")


def load_impl(repo: str, issue: str) -> tuple[Path, dict]:
    path = work_path(repo, issue, "impl")
    if not path.is_file():
        die(f"{lib.rel(path)} doesn't exist; `new plan {repo} {issue}` creates it")
    fm, _, _ = lib.split_frontmatter(lib.read_text(path))
    return path, fm or {}


def issue_branch_re(issue: str) -> re.Pattern:
    return re.compile(rf"^[^/]+/{issue}-")


def issue_context(args) -> str:
    """Validate <repo> <issue>; returns the repo's owner/name."""
    index = lib.Index()
    require_number(args.issue)
    require_repo(index, args.repo)
    return index.slugs[args.repo]


def add_planned_label(slug: str, issue: str) -> None:
    name, color, desc = lib.PLANNED_LABEL
    github.ensure_label(slug, name, color, desc)
    github.gh("issue", "edit", issue, "--repo", slug, "--add-label", name)


def cmd_seal_plan(args) -> None:
    slug = issue_context(args)
    info = github.issue_info(slug, args.issue)      # the one read: its `edited` is what the plan records (D6)
    if lib.FEATURE_LABEL[0] in info["labels"]:
        die(f"{args.repo}#{args.issue} is a feature (labelled '{lib.FEATURE_LABEL[0]}'); a feature is never "
            "planned, only its sub-issues are")
    path, fm, body = load_plan(args.repo, args.issue)
    name = lib.PLANNED_LABEL[0]
    if fm.get("status") == "sealed":
        if name in info["labels"]:
            die(f"{lib.rel(path)} is already sealed")
        add_planned_label(slug, args.issue)     # `stage` reports this state as backlog and points here
        print(f"the plan was already sealed; added the '{name}' label to {slug}#{args.issue}")
        return
    if fm.get("pr") == lib.NO_PR:
        paths = [f for f in plan_files(body) if f != "none"]
        if paths:
            die(f"{lib.rel(path)} declares pr: none, so no step may name a file; a step with nothing to edit "
                "says 'Files: none' (describe vault and GitHub work in Do:). Named: " + ", ".join(paths))
    trial = {**fm, "status": "sealed", "issue_updated": "2000-01-01T00:00:00Z", "base_sha": "0000000"}
    errors = lib.validate_file(path, text=lib.render_frontmatter(trial) + body.lstrip("\n")).errors
    if errors:
        die(f"{lib.rel(path)} can't be sealed yet:\n  " + "\n  ".join(errors))
    add_planned_label(slug, args.issue)     # a label never changes `edited` (D1), so `info` still holds
    sha = github.head_sha(slug, github.default_branch(slug))[:7]
    update_note(path, status="sealed", issue_updated=info["edited"], base_sha=sha)
    log_event(args.repo, args.issue, "planned")
    print(f"sealed {lib.rel(path)} (issue_updated {info['edited']}, base_sha {sha}); "
          f"added the '{name}' label")


def load_retro(repo: str, issue: str) -> tuple[Path, dict, str]:
    path = work_path(repo, issue, "retro")
    if not path.is_file():
        die(f"{lib.rel(path)} doesn't exist; `new retro {repo} {issue}` creates it")
    fm, body, _ = lib.split_frontmatter(lib.read_text(path))
    return path, fm or {}, body


AUDIT_LINE_RE = re.compile(r"^- (.+): (confirmed|rewritten|removed) — (.+)$")


def parse_audit_lines(body_lines: list[str]) -> list[tuple[str, str]]:
    """(heading, verb) for every '- <heading>: confirmed|rewritten|removed — <reason>' line."""
    out = []
    for ln in body_lines:
        m = AUDIT_LINE_RE.match(ln)
        if m:
            out.append((m.group(1), m.group(2)))
    return out


def audit_gaps(current: list[str], audit: list[tuple[str, str]]) -> tuple[list, list, list]:
    """(missing, duplicate, leftover) heading names, against the retro's audit lines (D6)."""
    counts = {}
    for h, _ in audit:
        counts[h] = counts.get(h, 0) + 1
    duplicate = sorted(h for h, n in counts.items() if n > 1)
    missing = sorted(h for h in current if h not in counts)
    removed = {h for h, v in audit if v == "removed"}
    leftover = sorted(h for h in removed if h in current)
    return missing, duplicate, leftover


def audit_problems(label: str, current: list[str], audit: list[tuple[str, str]]) -> list[str]:
    """Human-readable problems for one audited doc, from `audit_gaps`."""
    missing, dup, leftover = audit_gaps(current, audit)
    out = []
    if missing:
        out.append(f"{label}: missing an audit line for: {', '.join(missing)}")
    if dup:
        out.append(f"{label}: more than one audit line for: {', '.join(dup)}")
    if leftover:
        out.append(f"{label}: marked removed but still there: {', '.join(leftover)}")
    return out


FINDING_NUM_RE = re.compile(r"^- R(\d+) \((?:major|minor)\): ")


def finding_numbers(impl_body: str) -> tuple[set[int], set[int]]:
    """(all R numbers, R numbers whose finding is '→ left open') in an impl note's findings."""
    allnums, left_open = set(), set()
    for ln in dict(lib.split_sections(lib.blank_code_blocks(impl_body))).get("Review findings", []):
        m = FINDING_NUM_RE.match(ln)
        if m:
            allnums.add(int(m.group(1)))
            if ln.endswith("→ left open"):
                left_open.add(int(m.group(1)))
    return allnums, left_open


def followup_problems(impl_body: str, follow_lines: list[str]) -> list[str]:
    """Left-open findings with no follow-up line, findings settled twice, references to missing ones (D4, D5)."""
    allnums, left_open = finding_numbers(impl_body)
    counts: dict[int, int] = {}
    for ln in follow_lines:
        m = lib.FINDING_REFS_RE.search(ln)
        if m:
            for n in re.findall(r"R(\d+)", m.group(1)):
                counts[int(n)] = counts.get(int(n), 0) + 1

    def fmt(nums) -> str:
        return ", ".join(f"R{n}" for n in sorted(nums))

    out = []
    unsorted = left_open - set(counts)
    if unsorted:
        out.append(f"Follow-ups: no follow-up line for the left-open review finding(s) {fmt(unsorted)}; "
                   "end one with ' (R<n>)' (issue, idea or dropped)")
    twice = {n for n, c in counts.items() if c > 1}
    if twice:
        out.append(f"Follow-ups: more than one follow-up line for {fmt(twice)}")
    unknown = set(counts) - allnums
    if unknown:
        out.append(f"Follow-ups: the impl note has no review finding {fmt(unknown)}")
    return out


def lasting_problems(index: lib.Index, repo: str, issue: str) -> list[str]:
    """Lasting decisions of the issue's sealed plan that have no decision record yet, or whose record
    doesn't name the record the plan line says it replaces (#36)."""
    path = work_path(repo, issue, "plan")
    if not path.is_file():
        return []
    fm, body, _ = lib.split_frontmatter(lib.read_text(path))
    if (fm or {}).get("status") != "sealed":
        return []
    by_source = {r["fm"].get("source"): r for recs in index.decisions.values() for r in recs}
    out = []
    for k, replaces in lib.plan_lasting_decisions(body):
        source = f"plan {repo}#{issue} D{k}"
        rec = by_source.get(source)
        if rec is None:
            out.append(f"D{k} is a lasting decision with no record; run `decision add <project> \"<title>\" "
                       f"--decision \"...\" --why \"...\" --source \"{source}\""
                       + (f" --replaces {replaces}" if replaces else "") + "`")
        elif replaces and lib.link_target(rec["fm"].get("replaces")) != replaces:
            out.append(f"D{k} replaces [[{replaces}]], but its record {rec['stem']} doesn't; "
                       "that record can't be changed, so tell the user")
    return out


def trial_seal_errors(path: Path, fm: dict, body: str) -> list[str]:
    trial = {**fm, "status": "sealed"}
    return lib.validate_file(path, text=lib.render_frontmatter(trial) + body.lstrip("\n")).errors


def bump_subject(issue: str) -> str:
    return f"docs(claude): verify audited sections (#{issue})"


def check_merge_ready(top: Path, pr: str, pr_info: dict, issue: str) -> bool:
    """Refuse, before anything is committed, unless the PR's head is the local HEAD (or its parent, when HEAD
    is this issue's own bump commit, D5) and every check of that head passed (D3). Returns whether HEAD
    already is the bump commit (D6)."""
    local = github.git("rev-parse", "HEAD", cwd=top)
    bumped = github.git("log", "-1", "--format=%s", cwd=top) == bump_subject(issue)
    parent = github.git("rev-parse", "HEAD^", cwd=top) if bumped else None
    if pr_info["head"] not in (local, parent):
        die(f"PR #{pr}'s head is {pr_info['head'][:12] or '(unknown)'}, but the local {top.name} branch is at "
            f"{local[:12]}; push or pull until they match, then run this again")
    if pr_info["unpassed"]:
        die(f"PR #{pr} has checks that haven't passed: {', '.join(pr_info['unpassed'])}; "
            "wait for them (or fix them), then run this again")
    return bumped


def worktree_holding(top: Path, branch: str) -> Path | None:
    """The worktree that has `branch` checked out, if any (it can be this clone's own)."""
    path = None
    for ln in github.git("worktree", "list", "--porcelain", cwd=top).splitlines():
        if ln.startswith("worktree "):
            path = Path(ln[len("worktree "):])
        elif ln == f"branch refs/heads/{branch}" and path is not None:
            return path
    return None


def sync_default_branch(top: Path, default: str, pr: str) -> None:
    """After the merge: leave the clone on the default branch, pulled up to date, or pull it in the worktree
    that has it checked out (D8, D9, D12). Dies, with the commands to run by hand, if that fails."""
    pull = ["pull", "--ff-only", "origin", default]
    holder = worktree_holding(top, default)
    where = holder if holder is not None and holder.resolve() != top.resolve() else top
    steps = [] if where != top else [["switch", default]]
    try:
        for step in steps + [pull]:
            github.git(*step, cwd=where)
    except github.CmdError as e:
        by_hand = "".join(f"\n  git -C {where} {' '.join(s)}" for s in steps + [pull])
        die(f"PR #{pr} is merged and the notes are sealed, but {default} in {where} couldn't be brought up "
            f"to date: {e.stderr or e}\nfix that, then run:{by_hand}")


def cmd_seal_retro(args) -> None:
    index = lib.Index()
    require_number(args.issue)
    require_repo(index, args.repo)
    slug = index.slugs[args.repo]
    impl_path, impl_fm = load_impl(args.repo, args.issue)
    _, impl_body, _ = lib.split_frontmatter(lib.read_text(impl_path))
    retro_path, retro_fm, retro_body = load_retro(args.repo, args.issue)
    for path, fm in ((impl_path, impl_fm), (retro_path, retro_fm)):
        if fm.get("status") == "sealed":
            die(f"{lib.rel(path)} is already sealed")

    top = require_clone(slug)
    info = github.issue_info(slug, args.issue)
    closed = info["state"] != "OPEN"
    no_pr = plan_is_no_pr(args.repo, args.issue)
    if no_pr != (retro_fm.get("pr") == lib.NO_PR):
        die(f"{lib.rel(retro_path)} says pr: {retro_fm.get('pr')}, but the plan "
            f"{'declares pr: none' if no_pr else 'has a PR'}; they must agree")
    merging, bump_done, default = False, False, ""
    pr = retro_fm.get("pr", "")
    if not no_pr:     # an issue with a PR: the branch, the PR and the merge (a no-PR one has none of them)
        current_branch = github.git("branch", "--show-current", cwd=top)
        if closed:
            default = github.default_branch(slug)
            if current_branch == default or not current_branch:
                die(f"issue #{args.issue} is closed; check out a branch other than {default} "
                    f"(currently {current_branch or '(detached)'}; the impl note's own branch "
                    "may already be deleted)")
        else:
            branch = impl_fm.get("branch")
            if not branch:
                die(f"{lib.rel(impl_path)} has no 'branch:'; `branch {args.repo} {args.issue}` sets it")
            if current_branch != branch:
                die(f"the current branch is {current_branch or '(detached)'}, but {lib.rel(impl_path)} "
                    f"says {branch}; check out {branch} first")
        if github.git("status", "--porcelain", cwd=top):
            die("the working tree isn't clean; commit or stash your changes first")
        if not closed:
            pr_info = github.pr_closing(slug, pr)
            if (args.issue, slug.lower()) not in [(n, r.lower()) for n, r in pr_info["closes"]]:
                if pr_info["state"] == "OPEN":
                    die(f"PR #{pr} will not close issue #{args.issue} when it merges; restore a "
                        f"`Closes #{args.issue}` line in its description, then run this again")
                die(f"PR #{pr} is {pr_info['state']} and did not close issue #{args.issue}; "
                    "close the issue by hand, then run this again")
            merging = pr_info["state"] == "OPEN"
            if merging:
                default = github.default_branch(slug)
                bump_done = check_merge_ready(top, pr, pr_info, args.issue)

    claude_path = top / "CLAUDE.md"
    if not claude_path.is_file():
        die(f"{claude_path} doesn't exist")
    claude_text = lib.read_text(claude_path)
    sections, _ = claudemd.parse(claude_text)
    claude_headings = [s.name for s in sections]

    project_id = index.repos[args.repo][0]
    project_path = index.projects[project_id]
    _, overview_body, _ = lib.split_frontmatter(lib.read_text(project_path))
    overview_headings = [n for n, _ in lib.split_sections(lib.blank_code_blocks(overview_body))]

    readme_path = top / "README.md"
    readme_headings = None
    if readme_path.is_file():
        readme_headings = [n for n, _ in lib.split_sections(lib.blank_code_blocks(lib.read_text(readme_path)))]

    retro_secs = dict(lib.split_sections(lib.blank_code_blocks(retro_body)))
    claude_audit = parse_audit_lines(retro_secs.get("CLAUDE.md audit", []))
    readme_lines = retro_secs.get("README audit", [])
    readme_audit = parse_audit_lines(readme_lines)
    overview_audit = parse_audit_lines(retro_secs.get("Overview audit", []))

    problems = []
    for label, current, audit in (("CLAUDE.md", claude_headings, claude_audit),
                                  ("the project overview", overview_headings, overview_audit)):
        problems += audit_problems(label, current, audit)
    if readme_headings:
        problems += audit_problems("README.md", readme_headings, readme_audit)
    elif [ln.strip() for ln in readme_lines if ln.strip()] != [lib.NONE_LINE]:
        problems.append("README.md: no README.md here, or none with '##' headings; "
                        "the README audit must be 'None'")
    if problems:
        die(f"{lib.rel(retro_path)} doesn't cover every heading yet:\n  " + "\n  ".join(problems))
    follow_problems = followup_problems(impl_body, retro_secs.get("Follow-ups", []))
    if follow_problems:
        die(f"{lib.rel(retro_path)} has unsorted review findings:\n  " + "\n  ".join(follow_problems))

    lasting = lasting_problems(index, args.repo, args.issue)
    if lasting:
        die("the plan's lasting decisions aren't all recorded yet:\n  " + "\n  ".join(lasting))

    for path, fm, body in ((impl_path, impl_fm, impl_body), (retro_path, retro_fm, retro_body)):
        errors = trial_seal_errors(path, fm, body)
        if errors:
            die(f"{lib.rel(path)} can't be sealed yet:\n  " + "\n  ".join(errors))

    confirmed = {h for h, v in claude_audit if v == "confirmed"}
    new_text, bumped = claudemd.bump_verified(claude_text, sections, confirmed, lib.today())
    if no_pr or bump_done:      # no-PR: nothing to commit (D5); a rerun after a refused merge: HEAD already is the bump commit
        bumped = []
    if bumped:
        lib.write_text(claude_path, new_text)
        github.git("add", "CLAUDE.md", cwd=top)
        github.git("commit", "-q", "-m", bump_subject(args.issue), cwd=top)
    if bumped or bump_done:
        if closed and not github.git("rev-parse", "--abbrev-ref", "--symbolic-full-name",
                                     "@{u}", cwd=top, check=False):
            github.git("push", "-u", "origin", "HEAD", cwd=top)
        else:
            github.git("push", cwd=top)
    if merging:
        try:
            github.squash_merge(slug, pr, github.git("rev-parse", "HEAD", cwd=top))
        except github.CmdError as e:
            die(f"PR #{pr} was not merged, and nothing is sealed: {e.stderr or e}\n"
                "fix that, then run this again; it adds no second verified commit")

    if no_pr and not closed:
        try:
            github.close_issue(slug, args.issue, lib.closing_comment(retro_body))
        except github.CmdError as e:
            die(f"issue #{args.issue} was not closed, and nothing is sealed: {e.stderr or e}\n"
                "fix that, then run this again")

    update_note(impl_path, status="sealed")
    update_note(retro_path, status="sealed")
    update_note(project_path, audited=lib.today().isoformat())
    log_event(args.repo, args.issue, "retro-done")
    print(f"sealed {lib.rel(impl_path)} and {lib.rel(retro_path)}; set audited: {lib.today().isoformat()} "
          f"on {lib.rel(project_path)}" + (f"; bumped verified on: {', '.join(bumped)}" if bumped else "")
          + (f"; merged PR #{pr}" if merging else "")
          + (f"; closed issue #{args.issue}" if no_pr and not closed else ""))
    if merging:
        sync_default_branch(top, default, pr)


def cmd_claudemd_lint(args) -> None:
    if args.path:
        top = Path(args.path).resolve()
    else:
        found = github.git("rev-parse", "--show-toplevel", check=False)
        if not found:
            die("not inside a git clone; pass a repo path")
        top = Path(found)
    result = claudemd.lint(top)
    for w in result.warnings:
        print(f"warning: {w}")
    for e in result.errors:
        print(f"error: {e}")
    if result.errors:
        sys.exit(1)
    print(f"claudemd-lint OK ({result.stale} stale section(s))" if result.stale else "claudemd-lint OK")


def cmd_lint(args) -> None:
    """claudemd-lint, then the repo's declared `Lint:` command, from the clone's top
    level; both always run so one pass shows everything to fix (#33)."""
    found = github.git("rev-parse", "--show-toplevel", check=False)
    if not found:
        die("not inside a git clone")
    top = Path(found)
    result = claudemd.lint(top)
    for w in result.warnings:
        print(f"warning: {w}")
    for e in result.errors:
        print(f"error: {e}")
    failures = []
    if result.errors:
        failures.append(f"claudemd-lint failed; run it by hand with: cd {top} && {lib.CLI} claudemd-lint")
    failures += claudemd.run_lint_command(top)
    if failures:
        for f in failures:
            print(f"lint failed: {f}", file=sys.stderr)
        sys.exit(1)
    print("lint OK")


def plan_files(body: str) -> list[str]:
    """Paths named on the `Files:` lines of the plan's steps."""
    found = []
    for ln in lib.blank_code_blocks(body):
        m = re.match(r"^Files: ?(.*)", ln)
        if not m:
            continue
        ticked = re.findall(r"`([^`]+)`", m.group(1))
        found += ticked or [re.sub(r"\(.*?\)", "", t).strip() for t in m.group(1).split(",")]
    return sorted({f.replace("\\", "/").strip() for f in found if f.strip()})


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


def cmd_preflight(args) -> None:
    slug = issue_context(args)
    _, warnings = check_preflight(args.repo, args.issue, slug)
    for w in warnings:
        print(f"warning: {w}")
    print(f"preflight OK: {args.repo}#{args.issue} has a sealed plan and the issue is unchanged")


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


def branch_type(requested, is_bug: bool) -> str:
    if is_bug:
        if requested not in (None, "fix"):
            die(f"the issue is labelled `{lib.BUG_LABEL[0]}`, so its branch is `fix/`, not `{requested}/`")
        return "fix"
    if requested == "fix":
        die(f"`fix/` branches are only for an issue labelled `{lib.BUG_LABEL[0]}`; "
            f"if this issue is a bug, add the label on GitHub first")
    return requested or "feat"


def cmd_branch(args) -> None:
    slug = issue_context(args)
    refuse_no_pr(args.repo, args.issue, "there is nothing to branch")
    info, warnings = check_preflight(args.repo, args.issue, slug)
    for w in warnings:
        print(f"warning: {w}")
    btype = branch_type(args.type, lib.BUG_LABEL[0] in info["labels"])
    impl, _ = load_impl(args.repo, args.issue)
    top = require_clone(slug)
    name = f"{btype}/{args.issue}-{args.slug or default_slug(info['title'])}"
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


def cmd_open_pr(args) -> None:
    slug = issue_context(args)
    refuse_no_pr(args.repo, args.issue, "there is nothing to open")
    impl, fm = load_impl(args.repo, args.issue)
    branch = fm.get("branch")
    if not branch:
        die(f"{lib.rel(impl)} has no 'branch:'; `branch {args.repo} {args.issue}` sets it")
    top = require_clone(slug)
    current = github.git("branch", "--show-current", cwd=top)
    if current != branch:
        die(f"the current branch is {current or '(detached)'}, but {lib.rel(impl)} says {branch}; "
            f"check out {branch} first")
    if github.git("status", "--porcelain", cwd=top):
        die("the working tree isn't clean; commit or stash your changes first")
    extra = ""
    if args.body_file:
        try:
            extra = Path(args.body_file).read_text(encoding="utf-8")
        except OSError as e:
            die(f"can't read --body-file: {e}")
    info = github.issue_info(slug, args.issue)
    github.git("push", "-u", "origin", branch, cwd=top)
    existing = [p for p in github.open_prs(slug) if p["headRefName"] == branch]
    if existing:
        url, verb = existing[0]["url"], "found the open PR"
    else:
        body = f"Closes #{args.issue}\n" + (f"\n{extra.strip()}\n" if extra.strip() else "")
        url = create_with_body(["pr", "create", "--repo", slug, "--head", branch,
                                "--base", github.default_branch(slug), "--title", info["title"]], body)
        verb = "opened"
    m = re.search(r"/pull/(\d+)", url)
    if not m:
        die(f"couldn't read a PR number from {url!r}")
    update_note(impl, pr=m.group(1))
    if fm.get("pr") != m.group(1):
        log_event(args.repo, args.issue, "pr-opened", url)
    print(f"{verb} {url}; recorded pr: {m.group(1)} in {lib.rel(impl)}")


def create_with_body(cmd: list[str], body: str) -> str:
    """Run a `gh ... create` command with the body from a temp file; returns the URL it prints."""
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8", newline="\n") as f:
        f.write(body)
    try:
        out = github.gh(*cmd, "--body-file", f.name)
    finally:
        os.unlink(f.name)
    return out.strip().splitlines()[-1].strip()


def stage_of(slug: str, repo: str, issue: str) -> tuple[str, str]:
    """(stage, why) of an issue, derived from GitHub and the plan note (design §4)."""
    info = github.issue_info(slug, issue)
    if info["state"] != "OPEN":
        return "done", f"issue #{issue} is {info['state'].lower()}"
    if lib.FEATURE_LABEL[0] in info["labels"]:
        return "feature", f"{info['sub_total'] - info['sub_completed']} of {info['sub_total']} sub-issues open"
    pat = issue_branch_re(issue)
    prs = [p for p in github.open_prs(slug) if pat.match(p["headRefName"])]
    branches = [b for b in github.remote_branches(slug) if pat.match(b)]
    plan = work_path(repo, issue, "plan")
    sealed = False
    if plan.is_file():
        fm, _, _ = lib.split_frontmatter(lib.read_text(plan))
        sealed = (fm or {}).get("status") == "sealed"
    label = lib.PLANNED_LABEL[0]
    if prs:
        return "in review", f"PR {prs[0]['url']} is open for {prs[0]['headRefName']}"
    if branches:
        return "in progress", f"branch {branches[0]} exists and has no open PR"
    if sealed and label in info["labels"]:
        return "planned", f"the plan is sealed and the issue has the '{label}' label"
    if sealed:
        return "backlog", (f"the plan is sealed but the issue lacks the '{label}' label; "
                           f"rerun `seal plan {repo} {issue}` to add it")
    return "backlog", "the issue is open and has no sealed plan"


def cmd_stage(args) -> None:
    slug = issue_context(args)
    print("{}: {}".format(*stage_of(slug, args.repo, args.issue)))


def read_issue_body(name: str) -> str:
    """The body file's text: UTF-8 only, a leading BOM and CRLF endings normalised."""
    try:
        raw = Path(name).read_bytes()
    except OSError as e:
        die(f"can't read the body file {name}: {e.strerror or e}")
    try:
        return raw.decode("utf-8-sig").replace("\r\n", "\n")
    except UnicodeDecodeError:
        die(f"the body file {name} isn't UTF-8")


def idea_for_issue(index: lib.Index, idea_file: str, repo: str) -> Path:
    """The open idea an issue is filed from; its project, if any, must list the repo."""
    path = find_idea(idea_file)
    fm, _, _ = lib.split_frontmatter(lib.read_text(path))
    if not fm or fm.get("status") != "open":
        die(f"{lib.rel(path)} is {(fm or {}).get('status', 'not a valid idea')}; only open ideas can be promoted")
    project = fm.get("project")
    if project and project not in index.repos[repo]:
        die(f"repo '{repo}' doesn't belong to the idea's project '{project}'")
    return path


def checked_issue(index: lib.Index, repo: str, title: str, body_file: str) -> tuple[str, str]:
    """(title, body) of an issue to create, after the repo, title and the template are checked."""
    require_repo(index, repo)
    title = one_line(title)
    if not title:
        die("the title is empty")
    body = read_issue_body(body_file)
    problems = lib.issue_body_problems(body)
    if problems:
        print(f"error: {body_file} doesn't match the issue template:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        sys.exit(1)
    return title, body


def file_issue(index: lib.Index, repo: str, title: str, body_file: str, idea: Path | None = None,
               parent: str | None = None, bug: bool = False) -> None:
    """Create an issue from a body file that matches the template; with an idea, promote it; with a
    `parent` (`<n>` or `<repo>#<n>`), file it as the sub-issue of that open feature; without one it is
    standalone, labelled `bug` when `bug`. A design's story is refused without a parent.
    Everything is checked before `gh` runs. The body is posted exactly as the file has it."""
    title, body = checked_issue(index, repo, title, body_file)
    label = None
    if parent:
        parent_url = resolve_parent(index, repo, parent, body)
    else:
        parent_url = None
        if re.search(r"\[\[[^\]]+\]\] S[1-9]", "\n".join(source_lines({"body": body}))):
            die("this body's Source names a design's story; a story is filed under its design's feature "
                "with `--parent <ref>`, not with --standalone or --bug")
        if bug:
            label, color, desc = lib.BUG_LABEL
            github.ensure_label(index.slugs[repo], label, color, desc)
    url = create_with_body(github.issue_create_args(index.slugs[repo], title, parent=parent_url, label=label), body)
    if idea is not None:
        try:
            update_note(idea, status="promoted", promoted_to=url)
        except SystemExit:
            print(f"the issue was created: {url}", file=sys.stderr)
            raise
        log_idea_event("idea-promoted", title, idea)
        print(f"promoted {lib.rel(idea)} to {url}")
    print(url)


def resolve_parent(index: lib.Index, repo: str, value: str, body: str) -> str:
    """The URL of the open feature `value` (`<n>` or `<repo>#<n>`) names, as a parent for an issue of `repo`
    with this body; dies, before anything is created, when it can't be one (AC4)."""
    m = re.fullmatch(r"(?:([A-Za-z0-9._-]+)#)?([1-9][0-9]*)", value)
    if not m:
        die(f"--parent '{value}' must be an issue number or <repo>#<number>")
    parent_repo, number = m.group(1) or repo, m.group(2)
    if parent_repo not in index.slugs:
        die(f"repo '{parent_repo}' isn't registered in any project's repos:")
    if not set(index.repos[parent_repo]) & set(index.repos[repo]):
        die(f"repos '{parent_repo}' and '{repo}' share no project, so {parent_repo}#{number} can't be a parent")
    slug = index.slugs[parent_repo]
    info = github.issue_info(slug, number)
    if info["state"] != "OPEN":
        die(f"{parent_repo}#{number} is {info['state'].lower()}; a parent must be an open feature")
    if lib.FEATURE_LABEL[0] not in info["labels"]:
        die(f"{parent_repo}#{number} isn't labelled '{lib.FEATURE_LABEL[0]}'; only a feature can be a parent")
    source = "\n".join(source_lines({"body": body}))
    for design in dict.fromkeys(re.findall(r"\[\[([^\]]+)\]\] S[1-9]", source)):
        if design not in index.designs:
            die(f"the Source links [[{design}]] S<n>, but no design of that name is in the vault; "
                "fix the link before filing")
        found = feature_issues(project_issues(index, index.designs[design]["project"]), design)
        if not any(f_slug == slug and str(f["number"]) == number for f_slug, f in found):
            die(f"{parent_repo}#{number} isn't the feature of {design}; "
                + (f"it is {found[0][0]}#{found[0][1]['number']}" if found else
                   f"{design} has no feature yet: file it first with `issue create <repo> --feature {design}`"))
    return info["url"]


def source_lines(issue: dict) -> list[str]:
    return dict(lib.split_sections(lib.blank_code_blocks(issue.get("body") or ""))).get("Source", [])


def is_feature_of(issue: dict, design: str) -> bool:
    """Whether the issue's Source section links the design with no story number (D3)."""
    source = "\n".join(source_lines(issue))
    return f"[[{design}]]" in source and not re.search(rf"\[\[{re.escape(design)}\]\] S[1-9]", source)


def project_issues(index: lib.Index, project: str) -> list[tuple[str, dict]]:
    """(owner/name, issue) for every issue, open or closed, of the project's repos."""
    return [(index.slugs[name], issue) for name in sorted(n for n, pids in index.repos.items() if project in pids)
            for issue in github.repo_issues(index.slugs[name])]


def feature_issues(issues: list[tuple[str, dict]], design: str) -> list[tuple[str, dict]]:
    return [(slug, i) for slug, i in issues if is_feature_of(i, design)]


def filed_stories(issues: list[tuple[str, dict]], design: str) -> list[tuple[int, str, dict]]:
    """(story number, owner/name, issue) for each issue whose Source section names '[[<design>]] S<n>'.
    Only the Source section counts: a mention anywhere else is not a filing."""
    link = re.compile(rf"\[\[{re.escape(design)}\]\] S([1-9]\d*)\b")
    out = []
    for slug, issue in issues:
        found = {int(m.group(1)) for ln in source_lines(issue) for m in link.finditer(ln)}
        out += [(n, slug, issue) for n in sorted(found)]
    return sorted(out, key=lambda x: (x[0], x[1], x[2]["number"]))


def feature_design(index: lib.Index, repo: str, name: str) -> tuple[Path, str]:
    """(design path, project) of a sealed design whose project owns the repo, or die."""
    path = find_design(name)
    fm, _, _ = lib.split_frontmatter(lib.read_text(path))
    if (fm or {}).get("status") != "sealed":
        die(f"{lib.rel(path)} is a draft; a feature is filed for a sealed design (`seal design {path.stem}`)")
    if fm["project"] not in index.repos.get(repo, []):
        die(f"repo '{repo}' doesn't belong to the design's project '{fm['project']}'")
    return path, fm["project"]


def cmd_issue_create_feature(args, index: lib.Index) -> None:
    """File the design's feature issue and attach every filed story that has no parent (AC1-AC3)."""
    if args.idea:
        die("--feature can't be combined with --idea")
    path, project = feature_design(index, args.repo, args.feature)
    design = path.stem
    title, body = checked_issue(index, args.repo, args.title, args.body_file)
    source = "\n".join(source_lines({"body": body}))
    if f"[[{design}]]" not in source:
        die(f"the body's Source section must link [[{design}]]")
    if re.search(r"\[\[[^\]]+\]\] S[1-9]", source):
        die("a feature's Source names no story: remove the 'S<n>' after the design link")
    issues = project_issues(index, project)
    existing = feature_issues(issues, design)
    if existing:
        slug, issue = existing[0]
        die(f"{design} already has a feature: {slug}#{issue['number']} ({issue['state'].lower()}); "
            "change it on GitHub by hand")
    stories = filed_stories(issues, design)
    slug = index.slugs[args.repo]
    name, color, desc = lib.FEATURE_LABEL
    github.ensure_label(slug, name, color, desc)
    url = create_with_body(github.issue_create_args(slug, title, label=name), body)
    number = url.rstrip("/").rsplit("/", 1)[1]
    failed = []
    for n, story_slug, issue in stories:
        ref = f"{story_slug}#{issue['number']}"
        if issue.get("parent"):
            print(f"S{n} {ref} is already under {issue['parent']['url']}; left where it is")
            continue
        try:
            github.add_sub_issue(slug, number, issue["url"])
        except github.CmdError as e:
            failed.append(ref)
            print(f"S{n} {ref} could not be attached: {e.stderr or e}\n  run: gh issue edit {number} "
                  f"--repo {slug} --add-sub-issue {issue['url']}", file=sys.stderr)
            continue
        print(f"S{n} {ref} attached to {slug}#{number}")
    print(url)
    if failed:
        sys.exit(1)


def cmd_issue_create(args) -> None:
    index = lib.Index()
    require_repo(index, args.repo)
    if args.feature:
        return cmd_issue_create_feature(args, index)
    idea = idea_for_issue(index, args.idea, args.repo) if args.idea else None
    file_issue(index, args.repo, args.title, args.body_file, idea, args.parent, args.bug)


def cmd_idea_promote(args) -> None:
    index = lib.Index()
    path = find_idea(args.file)
    fm, body, _ = lib.split_frontmatter(lib.read_text(path))
    if not fm or fm.get("status") != "open":
        die(f"{lib.rel(path)} is {(fm or {}).get('status', 'not a valid idea')}; only open ideas can be promoted")
    project = fm.get("project")
    if args.repo:
        require_repo(index, args.repo)
        name = args.repo
    else:
        if not project:
            die("the idea has no project; pass --repo <name> to say where the issue goes")
        names = sorted(n for n, pids in index.repos.items() if project in pids)
        if len(names) != 1:
            die(f"project '{project}' has {len(names)} repos ({', '.join(names) or 'none'}); "
                "pass --repo <name>")
        name = names[0]
    heading = next((ln[2:].strip() for ln in body.splitlines() if ln.startswith("# ")), "")
    title = args.title or heading
    if not one_line(title):
        die("the idea has no title; pass --title")
    file_issue(index, name, title, args.body_file, idea_for_issue(index, args.file, name), args.parent, args.bug)


def hooks_path_state(top: Path, target: Path) -> tuple[str, str]:
    """(state, current core.hooksPath): 'unset', 'ours' (the shared copy), 'accepted'
    (another path whose pre-push is agent-vault's) or 'foreign'."""
    cur = github.git("config", "--local", "--get", "core.hooksPath", cwd=top, check=False)
    if not cur:
        return "unset", cur
    where = Path(cur) if Path(cur).is_absolute() else top / cur
    if os.path.normcase(os.path.realpath(where)) == os.path.normcase(os.path.realpath(target)):
        return "ours", cur
    try:
        marked = lib.HOOK_MARKER in (where / "pre-push").read_text(encoding="utf-8", errors="replace")
    except OSError:
        marked = False
    return ("accepted" if marked else "foreign"), cur


def cmd_repo_init(args) -> None:
    index = lib.Index()
    found = github.git("rev-parse", "--show-toplevel", cwd=args.path, check=False)
    if not found:
        die(f"{args.path} is not a git clone")
    top = Path(found)
    slug = github.origin_slug(top)
    name = slug.split("/")[1]
    if index.slugs.get(name, "").lower() != slug.lower():
        die(f"{slug} is not listed in any project's repos:. Add it to a project "
            f"(`new project <id> --repo {slug}`) before running repo-init")
    hooks = lib.INSTALL_DIR / "githooks"
    if not (hooks / "pre-push").is_file():
        die(f"{hooks / 'pre-push'} is missing; run install.py first")
    state, cur = hooks_path_state(top, hooks)
    if state == "foreign":
        die(f"core.hooksPath is already {cur}, and its pre-push isn't agent-vault's; not replacing "
            "it. Change or unset it by hand, then rerun repo-init")
    report = []

    have = github.gh_json("api", f"repos/{slug}")
    change = {k: v for k, v in REPO_SETTINGS.items() if have.get(k) != v}
    if change:
        fields = [a for k, v in change.items() for a in ("-F", f"{k}={'true' if v else 'false'}")]
        try:
            github.gh("api", "-X", "PATCH", f"repos/{slug}", *fields)
        except github.CmdError as e:
            if e.kind != "failed":
                raise
            die(f"GitHub refused the merge settings for {slug} ({e.stderr or e}). Set them by hand "
                f"{MANUAL_SETTINGS}, then rerun repo-init")
        report.append("merge settings: squash-only, delete branch on merge (updated)")
    else:
        report.append("merge settings: already squash-only with delete branch on merge")

    for label, color, desc in (lib.PLANNED_LABEL, lib.FEATURE_LABEL, lib.BUG_LABEL):
        made = github.ensure_label(slug, label, color, desc)
        report.append(f"label '{label}': " + ("created" if made else "already exists"))

    if state == "unset":
        github.git("config", "--local", "core.hooksPath", hooks.as_posix(), cwd=top)
        report.append(f"core.hooksPath: set to {hooks.as_posix()}")
    else:
        report.append(f"core.hooksPath: already {cur}")

    claude = top / "CLAUDE.md"
    if claude.exists():
        report.append("CLAUDE.md: already exists, left alone")
    else:
        tpl = (lib.INSTALL_DIR / "templates" / "repo-CLAUDE.md").read_text(encoding="utf-8")
        lib.write_text(claude, tpl.replace("{{PROJECT}}", index.repos[name][0]).replace("{{REPO}}", name))
        report.append("CLAUDE.md: wrote a skeleton (not committed; fill it in)")
    issue_tpl = top / lib.ISSUE_TEMPLATE_PATH
    if issue_tpl.exists():
        report.append(f"{lib.ISSUE_TEMPLATE_PATH}: already exists, left alone")
    else:
        lib.write_text(issue_tpl, lib.issue_template_file())
        report.append(f"{lib.ISSUE_TEMPLATE_PATH}: wrote the issue template (not committed)")
    print(f"repo-init {slug}:")
    print("\n".join("  " + r for r in report))


# --- ideas ---------------------------------------------------------------------
def read_head(path: Path) -> dict:
    """A note's frontmatter, read without touching its body."""
    head = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        if f.readline().strip() != "---":
            return {}
        head.append("---")
        for n, ln in enumerate(f):
            head.append(ln.rstrip("\n"))
            if ln.strip() == "---" or n > 60:
                break
    fm, _, _ = lib.split_frontmatter("\n".join(head))
    return fm or {}


def all_ideas() -> list[tuple[Path, dict]]:
    return [(p, read_head(p)) for p in sorted((AGENT / FOLDERS["idea"]).glob("*.md"))]


def open_idea_count() -> int:
    return sum(1 for _, fm in all_ideas() if fm.get("status") == "open")


def unique_idea_path(title: str) -> Path:
    slug = lib.slugify(title)[:SLUG_MAX].strip("-") or "untitled"
    base = f"{lib.today().isoformat()}-{slug}"
    path, n = AGENT / FOLDERS["idea"] / f"{base}.md", 1
    while path.exists():
        n += 1
        path = AGENT / FOLDERS["idea"] / f"{base}-{n}.md"
    return path


def add_idea(title: str, source: str, project: str | None, context: str = "") -> Path:
    """Create an idea note and log `idea-added`; shared by `idea add` and `handoff pull`."""
    title = lib.safe_title(one_line(title))
    path = unique_idea_path(title)
    commit_note(path, idea_note(title, source, project, context))
    log_idea_event("idea-added", title, path)
    return path


def cmd_idea_add(args) -> None:
    index = lib.Index()
    require_project(index, args.project)
    if not one_line(args.title):
        die("idea title is empty")
    if not lib.IDEA_SOURCE_RE.match(args.source):
        die("source must be 'retro <repo>#<n>', 'phone:<path>' or 'session'")
    print("created " + lib.rel(add_idea(args.title, args.source, args.project)))


def find_idea(name: str) -> Path:
    stem = Path(name.replace("\\", "/")).name
    stem = stem[:-3] if stem.lower().endswith(".md") else stem
    path = AGENT / FOLDERS["idea"] / f"{stem}.md"
    if not path.is_file():
        die(f"no idea '{stem}' in {FOLDERS['idea']}/; `ideas` lists them")
    return path


def cmd_idea_drop(args) -> None:
    path = find_idea(args.file)
    reason = one_line(args.reason)
    if not reason:
        die("--reason is empty")
    fm, body, _ = lib.split_frontmatter(lib.read_text(path))
    if not fm or fm.get("status") != "open":
        die(f"{lib.rel(path)} is {(fm or {}).get('status', 'not a valid idea')}; only open ideas can be dropped")
    lines = body.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    keep = lib.SIZE_CAPS["idea"] - 1
    if len(lines) > keep:
        print(f"note: cut {len(lines) - keep} trailing line(s) to fit the {lib.SIZE_CAPS['idea']}-line cap")
        lines = lines[:keep]
    fm["status"] = "dropped"
    commit_note(path, lib.render_frontmatter(fm) + "\n".join(lines + [f"Dropped: {reason}"]) + "\n")
    print("dropped " + lib.rel(path))


def print_ideas(rows: list[tuple[Path, dict]]) -> None:
    if not rows:
        print("no ideas")
    for p, fm in rows:
        proj = fm.get("project") or "-"
        print(f"{p.stem}  {fm.get('status', '?')}  project:{proj}  created {fm.get('created', '?')}")


def cmd_ideas(args) -> None:
    rows = [(p, fm) for p, fm in all_ideas()
            if not args.project or fm.get("project") == args.project]
    if args.action == "review":
        cutoff = lib.today() - dt.timedelta(days=REVIEW_DAYS)
        old = []
        for p, fm in rows:
            try:
                created = dt.date.fromisoformat(fm.get("created", ""))
            except ValueError:
                continue
            if fm.get("status") == "open" and created < cutoff:
                old.append((p, fm))
        print_ideas(old)
        return
    print_ideas([(p, fm) for p, fm in rows if fm.get("status") == args.status])


# --- decisions -----------------------------------------------------------------
def decision_note(project: str, title: str, decision: str, why: str, source: str, replaces: str | None) -> str:
    fm = {"type": "decision", "project": project, "status": "active",
          "created": lib.today().isoformat(), "source": source}
    if replaces:
        fm["replaces"] = f"[[{replaces}]]"
    return f"{lib.render_frontmatter(fm)}# {title}\n\n## Decision\n{decision}\n\n## Why\n{why}\n"


def with_standing_lines(text: str, lines: list[str]) -> str | None:
    """A project overview with its '## Standing decisions' body replaced by `lines`; None if it has no such heading."""
    src = text.split("\n")
    heading = f"## {lib.STANDING}"
    if heading not in src:
        return None
    start = src.index(heading)
    end = next((i for i in range(start + 1, len(src)) if src[i].startswith("## ")), len(src))
    return "\n".join(src[:start + 1] + lines + [""] + src[end:])


def find_decision(index: lib.Index, project: str, name: str) -> dict:
    """The record `name` ('<stem>', with or without .md or [[ ]]) of a project, or die."""
    stem = name.strip().removeprefix("[[").removesuffix("]]").removesuffix(".md")
    for rec in index.decisions.get(project, []):
        if rec["stem"] == stem:
            return rec
    other = [pid for pid, recs in index.decisions.items() if pid != project and any(r["stem"] == stem for r in recs)]
    if other:
        die(f"'{stem}' is a record of project '{other[0]}', not '{project}'")
    die(f"no decision record '{stem}' in project '{project}'; `decisions --all --project {project}` lists them")


def record_path(project: str, records: list[dict], title: str) -> Path:
    """Where the project's next decision record goes."""
    n = max((r["n"] for r in records), default=0) + 1
    slug = lib.slugify(title)[:SLUG_MAX].strip("-") or "untitled"
    return AGENT / FOLDERS["decision"] / project / f"{project}-d{n}-{slug}.md"


def write_record(path: Path, project: str, title: str, decision: str, why: str, source: str,
                 old: dict | None) -> None:
    """Create a decision record, and mark the record it replaces superseded. Shared by `decision add`
    and `seal design`; the callers keep the overview and the log in step."""
    commit_note(path, decision_note(project, title, decision, why, source, old["stem"] if old else None))
    if old:
        update_note(old["path"], status="superseded", superseded_by=f"[[{path.stem}]]")


def refresh_standing(project: str, overview: Path) -> None:
    lines = lib.standing_lines(lib.Index().decisions.get(project, []))
    commit_note(overview, with_standing_lines(lib.read_text(overview), lines))


def snapshot(paths: list[Path]) -> dict:
    """Each path's text now, or None if it doesn't exist yet, for `restore`."""
    return {p: (lib.read_text(p) if p.exists() else None) for p in paths}


def restore(saved: dict) -> None:
    for p, text in saved.items():
        if text is None:
            p.unlink(missing_ok=True)
        else:
            lib.write_text(p, text)


def cmd_decision_add(args) -> None:
    index = lib.Index()
    require_project(index, args.project)
    title = args.title.strip()
    if not title:
        die("the title is empty")
    if re.search(r"[\r\n]", title) or "[[" in title or "]]" in title:
        die("the title must be one line and must not contain [[ or ]]")
    decision, why = one_line(args.decision), one_line(args.why)
    if not decision:
        die("--decision is empty")
    if not why:
        die("--why is empty")
    source = args.source.strip()
    if not lib.DECISION_SOURCE_RE.match(source):
        die("source must be 'plan <repo>#<n> D<k>', 'design <design> D<k>', 'issue <repo>#<n>', "
            "'retro <repo>#<n>' or 'session'")
    if lib.UNIQUE_SOURCE_RE.match(source):
        for recs in index.decisions.values():
            for rec in recs:
                if rec["fm"].get("source") == source:
                    die(f"{source} is already recorded as {rec['stem']}")
    old = find_decision(index, args.project, args.replaces) if args.replaces else None
    if old and old["fm"].get("status") != "active":
        die(f"{old['stem']} is already superseded; replace the record that replaced it instead")
    records = index.decisions.get(args.project, [])
    overview = index.projects[args.project]
    if with_standing_lines(lib.read_text(overview), []) is None:
        die(f"{lib.rel(overview)} has no '## {lib.STANDING}' section to keep up to date")
    path = record_path(args.project, records, title)
    saved = snapshot([path, overview] + ([old["path"]] if old else []))
    try:
        write_record(path, args.project, title, decision, why, source, old)
        refresh_standing(args.project, overview)
    except SystemExit:
        restore(saved)
        raise
    append_log(f"- {lib.now_hhmm()} {lib.DECISION_EVENT} — {title} [[{path.stem}]]")
    print("created " + lib.rel(path))
    if old:
        print(f"superseded {lib.rel(old['path'])}")
    print(f"rewrote '## {lib.STANDING}' in {lib.rel(overview)}")


def find_design(name: str) -> Path:
    """The design '<stem>' (with or without .md), or die."""
    stem = Path(name.replace("\\", "/")).name
    stem = stem[:-3] if stem.lower().endswith(".md") else stem
    found = sorted((AGENT / FOLDERS["design"]).glob(f"*/{stem}.md"))
    if not found or not lib.DESIGN_NAME_RE.match(stem):
        die(f"no design '{stem}' in {FOLDERS['design']}/; `new design <project> \"<title>\"` creates one")
    return found[0]


def seal_design_problems(index: lib.Index, path: Path, fm: dict, body: str) -> list[str]:
    """Everything that stops a design being sealed, checked before any write."""
    project, problems = fm.get("project", ""), []
    if project not in index.projects:
        return [f"project '{project}' does not exist; create it (`new project {project}`) before sealing"]
    trial = {**fm, "status": "sealed", "sealed": lib.today().isoformat()}
    for e in lib.validate_file(path, index, lib.render_frontmatter(trial) + body.lstrip("\n")).errors:
        if not e.startswith("[X-DESIGN-SEALED]"):       # holds only once the records and promotions exist
            problems.append(e)
    records = {r["stem"]: r for r in index.decisions.get(project, [])}
    recorded = {r["fm"].get("source"): r["stem"] for recs in index.decisions.values() for r in recs}
    replaced = {}
    for d in lib.design_decisions(body):
        source = f"design {path.stem} D{d['n']}"
        if source in recorded:
            problems.append(f"D{d['n']} is already recorded as {recorded[source]}")
        old = d["replaces"]
        if old is None:
            continue
        if old not in records:
            problems.append(f"D{d['n']} replaces [[{old}]], which is not a record of project '{project}'")
        elif records[old]["fm"].get("status") != "active":
            problems.append(f"D{d['n']} replaces [[{old}]], which is already superseded")
        elif old in replaced:
            problems.append(f"D{d['n']} and D{replaced[old]} both replace [[{old}]]")
        replaced.setdefault(old, d["n"])
    for name in lib.design_ideas(body):
        ifm = index.ideas.get(name)
        if ifm is None:
            problems.append(f"idea [[{name}]] does not exist in {FOLDERS['idea']}/")
        elif ifm.get("status") != "open":
            problems.append(f"idea [[{name}]] is {ifm.get('status')}; only open ideas can be promoted")
        elif ifm.get("project") not in (None, "", project):
            problems.append(f"idea [[{name}]] belongs to project '{ifm.get('project')}', not '{project}'")
    repos = sorted(n for n, pids in index.repos.items() if project in pids)
    for s in lib.design_stories(body):
        if s["repo"] is not None and s["repo"] not in repos:
            problems.append(f"S{s['n']}'s repo '{s['repo']}' is not one of project '{project}': "
                            f"{', '.join(repos) or 'it has no repos'}")
        elif s["repo"] is None and len(repos) > 1:
            problems.append(f"S{s['n']} has no 'Repo:' line, and project '{project}' has {len(repos)} repos "
                            f"({', '.join(repos)})")
    if with_standing_lines(lib.read_text(index.projects[project]), []) is None:
        problems.append(f"{lib.rel(index.projects[project])} has no '## {lib.STANDING}' section to keep up to date")
    return problems


def cmd_seal_design(args) -> None:
    path = find_design(args.design)
    fm, body, _ = lib.split_frontmatter(lib.read_text(path))
    fm = fm or {}
    if fm.get("status") == "sealed":
        die(f"{lib.rel(path)} is already sealed")
    index = lib.Index()
    problems = seal_design_problems(index, path, fm, body)
    if problems:
        die(f"{lib.rel(path)} can't be sealed yet:\n  " + "\n  ".join(problems))

    project, overview = fm["project"], index.projects[fm["project"]]
    ideas = [AGENT / FOLDERS["idea"] / f"{name}.md" for name in lib.design_ideas(body)]
    saved = snapshot([path, overview, lib.daily_path(), *ideas,
                      *[r["path"] for r in index.decisions.get(project, [])]])
    created = []
    try:
        for d in lib.design_decisions(body):
            records = lib.Index().decisions.get(project, [])
            old = next((r for r in records if r["stem"] == d["replaces"]), None)
            rec = record_path(project, records, d["title"])
            created.append(rec)
            saved.setdefault(rec, None)
            write_record(rec, project, d["title"], d["decision"], d["why"], f"design {path.stem} D{d['n']}", old)
            append_log(f"- {lib.now_hhmm()} {lib.DECISION_EVENT} — {d['title']} [[{rec.stem}]]")
        for idea in ideas:
            _, idea_body, _ = lib.split_frontmatter(lib.read_text(idea))
            update_note(idea, status="promoted", promoted_to=f"[[{path.stem}]]")
            log_idea_event("idea-promoted", lib.decision_title(idea_body) or idea.stem, idea)
        refresh_standing(project, overview)
        append_log(f"- {lib.now_hhmm()} {lib.DESIGN_EVENT} — {lib.decision_title(body) or path.stem} [[{path.stem}]]")
        sealed = {**fm, "status": "sealed", "sealed": lib.today().isoformat()}
        commit_note(path, lib.render_frontmatter(sealed) + body.lstrip("\n"))
    except SystemExit:
        restore(saved)
        raise
    print(f"sealed {lib.rel(path)}: {len(created)} decision record(s), {len(ideas)} idea(s) promoted; "
          f"rewrote '## {lib.STANDING}' in {lib.rel(overview)}")


def cmd_design_stories(args) -> None:
    path = find_design(args.design)
    fm, body, _ = lib.split_frontmatter(lib.read_text(path))
    if (fm or {}).get("status") != "sealed":
        die(f"{lib.rel(path)} is a draft; stories are filed from a sealed design (`seal design {path.stem}`)")
    index = lib.Index()
    project = fm["project"]
    repos = sorted(n for n, pids in index.repos.items() if project in pids)
    issues = project_issues(index, project)
    features = feature_issues(issues, path.stem)
    filed: dict[int, list[tuple[str, dict]]] = {}
    for n, slug, issue in filed_stories(issues, path.stem):
        filed.setdefault(n, []).append((slug, issue))
    feature_url = features[0][1]["url"] if features else None
    stories = lib.design_stories(body)
    if features:
        slug, issue = features[0]
        print(f"feature: {slug}#{issue['number']} ({issue['state'].lower()})")
    else:
        print("no feature yet")
    for s in stories:
        found = filed.get(s["n"])
        print(f"S{s['n']}. {s['title']}: "
              + (", ".join(f"{slug}#{i['number']} ({i['state'].lower()})"
                           + ("" if feature_url is None or (i.get("parent") or {}).get("url") == feature_url
                              else " [not under the feature]") for slug, i in found)
                 if found else "not filed"))
    if not repos:
        print(f"project '{project}' has no repos yet, so nothing can be filed; register one first")
    unfiled = [s for s in stories if s["n"] not in filed]
    print(f"{len(stories) - len(unfiled)} of {len(stories)} stories filed" if stories else "no stories")


def feature_to_close(index: lib.Index, slug: str, issue: str, info: dict) -> tuple[dict | None, str]:
    """(feature, reason): the open feature that sealing `slug`#`issue` finishes, as {slug, number, title}, else
    None and why not (#59 D1). Never dies; a GitHub failure raises github.CmdError."""
    if not info.get("parent"):
        return None, "the issue has no parent"
    m = re.match(r"https://github\.com/([^/]+/[^/]+)/issues/(\d+)$", info["parent"]["url"])
    if not m:
        return None, f"the parent {info['parent']['url']} isn't a GitHub issue URL"
    fslug, fnum = m.group(1), m.group(2)
    ref = f"{fslug}#{fnum}"
    feat = github.feature_view(fslug, fnum)
    if feat["state"] != "OPEN":
        return None, f"the parent {ref} is {feat['state'].lower()}"
    if lib.FEATURE_LABEL[0] not in feat["labels"]:
        return None, f"the parent {ref} isn't labelled '{lib.FEATURE_LABEL[0]}'"
    links = [d for ln in source_lines(feat) for d in re.findall(r"\[\[([^\]|#]+)", ln)]
    design = next((d for d in links if is_feature_of(feat, d)), None)
    if design is None:
        return None, f"the feature {ref} has no design in its Source section"
    found = index.designs.get(design)
    if found is None:
        return None, f"the feature's design {design} isn't in the vault"
    if (found["fm"] or {}).get("status") != "sealed":
        return None, f"the feature's design {design} isn't sealed"
    _, design_body, _ = lib.split_frontmatter(lib.read_text(found["path"]))
    filed = {n for n, _, _ in filed_stories(project_issues(index, found["project"]), design)}
    unfiled = [f"S{s['n']}" for s in lib.design_stories(design_body) if s["n"] not in filed]
    if unfiled:
        return None, f"{', '.join(unfiled)} of {design} not filed yet"
    me = (slug.lower(), str(issue))
    open_subs = [f"{s['slug']}#{s['number']}" for s in feat["subs"]
                 if s["state"] == "OPEN" and (s["slug"].lower(), s["number"]) != me]
    if open_subs:
        return None, f"still open: {', '.join(open_subs)}"
    return {"slug": fslug, "number": fnum, "title": feat["title"]}, ""


def cmd_closes_feature(args) -> None:
    slug = issue_context(args)
    try:
        feature, reason = feature_to_close(lib.Index(), slug, args.issue, github.issue_info(slug, args.issue))
    except github.CmdError as e:
        die(f"couldn't check: {e.stderr or e}")
    if feature:
        print(f"sealing closes feature {feature['slug']}#{feature['number']}: {feature['title']}")
    else:
        print(f"sealing closes no feature: {reason}")


def cmd_features(args) -> None:
    index = lib.Index()
    require_project(index, args.project)
    names = sorted(n for n, pids in index.repos.items() if not args.project or args.project in pids)
    rows = []
    for name in names:
        for f in github.open_features(index.slugs[name], lib.FEATURE_LABEL[0]):
            designs = [d for ln in source_lines(f) for d in re.findall(r"\[\[([^\]]+)\]\]", ln)]
            link = f" [[{designs[0]}]]" if designs else ""
            rows.append(f"{name}#{f['number']} {one_line(f['title'])}{link} "
                        f"({f['total'] - f['completed']} of {f['total']} open)")
    print("\n".join(rows) if rows else "no open features")


def cmd_decisions(args) -> None:
    index = lib.Index()
    require_project(index, args.project)
    rows = [r for pid, recs in sorted(index.decisions.items()) if not args.project or pid == args.project
            for r in recs if args.all or r["fm"].get("status") == "active"]
    if not rows:
        print("no decisions")
    for r in rows:
        print(f"{r['stem']}.md  {r['title']}  [{r['fm'].get('source')}]"
              + ("  (superseded)" if r["fm"].get("status") != "active" else ""))


# --- log ---------------------------------------------------------------------
def cmd_log(args) -> None:
    index = lib.Index()
    require_project(index, args.project)
    text = one_line(args.text)
    if not text:
        die("log text is empty")
    entry = f"- {lib.now_hhmm()} session — {text}"
    if args.project:
        entry += f" [[{args.project}]]"
    path = append_log(entry)
    print(f"logged to {lib.rel(path)}: {entry}")


# --- handoff ---------------------------------------------------------------------
def load_handoffs() -> dict:
    try:
        return json.loads(lib.read_text(lib.HANDOFF_LOG))
    except Exception:
        return {}


def phone_notes() -> list[Path]:
    out = []
    for p in VAULT.rglob("*.md"):
        if lib.is_hidden(p) or p.parent == VAULT and p.name == "CLAUDE.md":
            continue
        if p.relative_to(VAULT).parts[0] == AGENT.name:
            continue
        out.append(p)
    return sorted(out, key=lambda p: p.stat().st_mtime, reverse=True)


def cmd_handoff_list(_args) -> None:
    done = load_handoffs()
    pending = [p for p in phone_notes() if lib.rel(p) not in done]
    if not pending:
        print("no phone-side notes waiting")
    for p in pending[:25]:
        when = dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d")
        print(f"{when}  {lib.rel(p)}")
    if len(pending) > 25:
        print(f"... and {len(pending) - 25} more")


def cmd_handoff_pull(args) -> None:
    index = lib.Index()
    require_project(index, args.project)
    given = Path(args.path)
    src = given if given.is_absolute() else VAULT / given
    parts = lib.vault_parts(src)
    if parts is None:
        die(f"'{args.path}' is outside the vault")
    if not src.is_file():
        die(f"'{args.path}' is not a file in the vault")
    if parts[0] == AGENT.name.lower():
        die(f"'{args.path}' is inside {AGENT.name}/; only phone-side notes can be pulled")
    if any(x.startswith(".") for x in parts) or parts == ["claude.md"]:
        die(f"'{args.path}' is not a phone-side note")
    rel_path = Path(os.path.relpath(os.path.realpath(src), os.path.realpath(VAULT))).as_posix()
    done = load_handoffs()
    if rel_path in done:
        die(f"'{rel_path}' was already pulled (idea {done[rel_path].get('idea', '?') if isinstance(done[rel_path], dict) else done[rel_path]})")
    title = lib.safe_title(Path(rel_path).stem)
    idea = add_idea(title, f"phone:{rel_path}", args.project, f"From [[{Path(rel_path).stem}]]")
    done[rel_path] = {"idea": idea.stem, "pulled": lib.today().isoformat()}
    lib.write_text(lib.HANDOFF_LOG, json.dumps(done, indent=2, ensure_ascii=False) + "\n")
    print(f"pulled {rel_path} -> {lib.rel(idea)}")


# --- validate / status / schema ---------------------------------------------------------
def cmd_validate(_args) -> None:
    errors, warnings = lib.validate_all()
    for kind, found in (("warning", warnings), ("error", errors)):
        for path, probs in found.items():
            for p in probs:
                print(f"{path}: {kind}: {p}")
    if errors:
        sys.exit(1)
    if not warnings:
        print("vault OK")


def purpose_line(path: Path) -> str:
    _, body, _ = lib.split_frontmatter(lib.read_text(path))
    lines = body.splitlines()
    for i, ln in enumerate(lines):
        if ln.strip() == "## Purpose":
            for nxt in lines[i + 1:]:
                if nxt.startswith("## "):
                    break
                if nxt.strip():
                    return nxt.strip()[:100]
    return "(no purpose written)"


def status_text() -> str:
    index = lib.Index()
    groups = {"active": [], "paused": []}
    for pid, path in sorted(index.projects.items()):
        fm, _, _ = lib.split_frontmatter(lib.read_text(path))
        st = (fm or {}).get("status")
        if st in groups:
            repos = fm.get("repos")
            repos = ", ".join(repos) if isinstance(repos, list) and repos else "no repos"
            groups[st].append(f"- [[{pid}]] — {purpose_line(path)} ({repos})")
    out = ["Active projects:"] + (groups["active"] or ["- (none)"])
    if groups["paused"]:
        out += ["Paused projects:"] + groups["paused"]
    out.append(f"Open ideas: {open_idea_count()}")
    return "\n".join(out)


def cmd_status(_args) -> None:
    print(status_text())


def cmd_schema(_args) -> None:
    print(lib.schema_text())


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
            p.add_argument("--type", choices=[t for t in lib.BRANCH_TYPES if t != "hotfix"],
                           help="default: fix for an issue labelled bug, else feat")
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
    p = sub.add_parser("closes-feature", help="whether sealing this issue's retro closes its finished feature")
    p.add_argument("repo")
    p.add_argument("issue")
    p.set_defaults(fn=cmd_closes_feature)
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


def main() -> None:
    lib.setup_io()
    if lib.VAULT is None and sys.argv[1:2] != ["lint"]:      # `lint` reads nothing from the vault
        die(f"no usable vault: {lib.CONFIG} is missing, or its vault path has no .obsidian folder; "
            "run install.py --vault <path>")
    args = build_parser().parse_args()
    try:
        args.fn(args)
    except github.CmdError as e:
        die(str(e))


if __name__ == "__main__":
    main()
