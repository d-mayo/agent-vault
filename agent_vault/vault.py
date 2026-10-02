"""The one way to create, move, and check notes in Agent/.

Usage: <CLI> <command>, where <CLI> is the exact command in the vault's
CLAUDE.md (the installer's Python plus this file). Commands:
  new project <id> [--repo owner/name ...] [--purpose "..."]
  new plan <repo> <issue>
  new retro <repo> <issue>
  seal plan <repo> <issue>
  seal retro <repo> <issue>
  preflight <repo> <issue>
  branch <repo> <issue> [--type feat] [--slug <slug>]
  open-pr <repo> <issue> [--body-file <file>]
  stage <repo> <issue>
  idea add "<title>" [--project <id>] [--source <source>]
  idea drop <file> --reason "..."
  idea promote <file> --body-file <file> [--title "..."] [--repo <name>]
  issue create <repo> --title "..." --body-file <file> [--idea <file>]
  ideas [--project <id>] [--status open|promoted|dropped]
  ideas review
  handoff list
  handoff pull "<phone path>" [--project <id>]
  log "what happened" [--project <id>]
  repo-init <path>
  claudemd-lint [<repo path>]
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
from lib import AGENT, FOLDERS, VAULT

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
            "## Standing decisions\n(none yet)\n")


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
    fm = lib.render_frontmatter({"type": "retro", "repo": repo, "issue": issue, "pr": pr, "status": "open"})
    return (f"{fm}# {repo}-{issue}-retro\n\n"
            "## Summary\n<!-- What was built and how it went. -->\n\n"
            "## CLAUDE.md audit\n\n"
            "## README audit\n\n"
            "## Overview audit\n\n"
            "## Follow-ups\nNone\n")


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
    if not isinstance(pr, str) or not lib.NUMBER_RE.match(pr):
        die(f"{lib.rel(impl)} has no 'pr:' yet; open the PR first")
    commit_note(retro, retro_note(args.repo, args.issue, pr))
    print("created " + lib.rel(retro))


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
    path, fm, body = load_plan(args.repo, args.issue)
    name = lib.PLANNED_LABEL[0]
    if fm.get("status") == "sealed":
        if name in github.issue_info(slug, args.issue)["labels"]:
            die(f"{lib.rel(path)} is already sealed")
        add_planned_label(slug, args.issue)     # `stage` reports this state as backlog and points here
        print(f"the plan was already sealed; added the '{name}' label to {slug}#{args.issue}")
        return
    trial = {**fm, "status": "sealed", "issue_updated": "2000-01-01T00:00:00Z", "base_sha": "0000000"}
    errors = lib.validate_file(path, text=lib.render_frontmatter(trial) + body.lstrip("\n")).errors
    if errors:
        die(f"{lib.rel(path)} can't be sealed yet:\n  " + "\n  ".join(errors))
    add_planned_label(slug, args.issue)
    info = github.issue_info(slug, args.issue)          # after the label: D1 ignores label changes anyway
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


def trial_seal_errors(path: Path, fm: dict, body: str) -> list[str]:
    trial = {**fm, "status": "sealed"}
    return lib.validate_file(path, text=lib.render_frontmatter(trial) + body.lstrip("\n")).errors


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
    current_branch = github.git("branch", "--show-current", cwd=top)
    closed = info["state"] != "OPEN"
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

    for path, fm, body in ((impl_path, impl_fm, impl_body), (retro_path, retro_fm, retro_body)):
        errors = trial_seal_errors(path, fm, body)
        if errors:
            die(f"{lib.rel(path)} can't be sealed yet:\n  " + "\n  ".join(errors))

    confirmed = {h for h, v in claude_audit if v == "confirmed"}
    new_text, bumped = claudemd.bump_verified(claude_text, sections, confirmed, lib.today())
    if bumped:
        lib.write_text(claude_path, new_text)
        github.git("add", "CLAUDE.md", cwd=top)
        github.git("commit", "-q", "-m", f"docs(claude): verify audited sections (#{args.issue})", cwd=top)
        if closed and not github.git("rev-parse", "--abbrev-ref", "--symbolic-full-name",
                                     "@{u}", cwd=top, check=False):
            github.git("push", "-u", "origin", "HEAD", cwd=top)
        else:
            github.git("push", cwd=top)

    update_note(impl_path, status="sealed")
    update_note(retro_path, status="sealed")
    update_note(project_path, audited=lib.today().isoformat())
    log_event(args.repo, args.issue, "retro-done")
    print(f"sealed {lib.rel(impl_path)} and {lib.rel(retro_path)}; set audited: {lib.today().isoformat()} "
          f"on {lib.rel(project_path)}" + (f"; bumped verified on: {', '.join(bumped)}" if bumped else ""))


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


def cmd_branch(args) -> None:
    slug = issue_context(args)
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


def cmd_open_pr(args) -> None:
    slug = issue_context(args)
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
    pat = issue_branch_re(issue)
    prs = [p for p in github.open_prs(slug) if pat.match(p["headRefName"])]
    branches = [b for b in github.remote_branches(slug) if pat.match(b)]
    plan = work_path(repo, issue, "plan")
    sealed = False
    if plan.is_file():
        fm, _, _ = lib.split_frontmatter(lib.read_text(plan))
        sealed = (fm or {}).get("status") == "sealed"
    label = lib.PLANNED_LABEL[0]
    if info["state"] != "OPEN":
        return "done", f"issue #{issue} is {info['state'].lower()}"
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


def file_issue(index: lib.Index, repo: str, title: str, body_file: str, idea: Path | None = None) -> None:
    """Create an issue from a body file that matches the template; with an idea, promote it.
    Everything is checked before `gh` runs. The body is posted exactly as the file has it."""
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
    url = create_with_body(["issue", "create", "--repo", index.slugs[repo], "--title", title], body)
    if idea is not None:
        try:
            update_note(idea, status="promoted", promoted_to=url)
        except SystemExit:
            print(f"the issue was created: {url}", file=sys.stderr)
            raise
        log_idea_event("idea-promoted", title, idea)
        print(f"promoted {lib.rel(idea)} to {url}")
    print(url)


def cmd_issue_create(args) -> None:
    index = lib.Index()
    require_repo(index, args.repo)
    idea = idea_for_issue(index, args.idea, args.repo) if args.idea else None
    file_issue(index, args.repo, args.title, args.body_file, idea)


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
    file_issue(index, name, title, args.body_file, idea_for_issue(index, args.file, name))


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

    label, color, desc = lib.PLANNED_LABEL
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
    for i, l in enumerate(lines):
        if l.strip() == "## Purpose":
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
        dest="kind", required=True, metavar="project|plan|retro")
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

    seal = sub.add_parser("seal", help="seal a plan or a retro").add_subparsers(
        dest="kind", required=True, metavar="plan|retro")
    for kind, fn in (("plan", cmd_seal_plan), ("retro", cmd_seal_retro)):
        p = seal.add_parser(kind)
        p.add_argument("repo")
        p.add_argument("issue")
        p.set_defaults(fn=fn)
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
    p = sub.add_parser("repo-init", help="set up a repo's merge settings, label, hooks and CLAUDE.md")
    p.add_argument("path")
    p.set_defaults(fn=cmd_repo_init)
    p = sub.add_parser("claudemd-lint", help="check a repo's CLAUDE.md (default: the current clone)")
    p.add_argument("path", nargs="?")
    p.set_defaults(fn=cmd_claudemd_lint)

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
    p.add_argument("--body-file", required=True, help="the issue body, in the issue template's sections")
    p.set_defaults(fn=cmd_idea_promote)

    issue = sub.add_parser("issue", help="create an issue from a templated body").add_subparsers(
        dest="action", required=True, metavar="create")
    p = issue.add_parser("create", help="refuses a body that doesn't match the issue template")
    p.add_argument("repo")
    p.add_argument("--title", required=True)
    p.add_argument("--body-file", required=True)
    p.add_argument("--idea", help="an open idea to mark promoted")
    p.set_defaults(fn=cmd_issue_create)

    p = sub.add_parser("ideas", help="list ideas, or `ideas review` for old open ones")
    p.add_argument("action", nargs="?", choices=["review"])
    p.add_argument("--project")
    p.add_argument("--status", default="open", choices=lib.STATUSES["idea"])
    p.set_defaults(fn=cmd_ideas)

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
    if lib.VAULT is None:
        die(f"no usable vault: {lib.CONFIG} is missing, or its vault path has no .obsidian folder; "
            "run install.py --vault <path>")
    args = build_parser().parse_args()
    try:
        args.fn(args)
    except github.CmdError as e:
        die(str(e))


if __name__ == "__main__":
    main()
