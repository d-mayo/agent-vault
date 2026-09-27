"""The one way to create, move, and check notes in Agent/.

Usage: <CLI> <command>, where <CLI> is the exact command in the vault's
CLAUDE.md (the installer's Python plus this file). Commands:
  new project <id> [--repo owner/name ...] [--purpose "..."]
  new plan <repo> <issue>
  new retro <repo> <issue>
  idea add "<title>" [--project <id>] [--source <source>]
  idea drop <file> --reason "..."
  ideas [--project <id>] [--status open|promoted|dropped]
  ideas review
  handoff list
  handoff pull "<phone path>" [--project <id>]
  log "what happened" [--project <id>]
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
from pathlib import Path

sys.dont_write_bytecode = True  # keep __pycache__ out of iCloud
import lib  # noqa: E402
from lib import AGENT, FOLDERS, VAULT

REVIEW_DAYS = 90            # `ideas review` lists open ideas older than this
SLUG_MAX = 60


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

    idea = sub.add_parser("idea", help="add or drop an idea").add_subparsers(
        dest="action", required=True, metavar="add|drop")
    p = idea.add_parser("add")
    p.add_argument("title")
    p.add_argument("--project")
    p.add_argument("--source", default="session")
    p.set_defaults(fn=cmd_idea_add)
    p = idea.add_parser("drop")
    p.add_argument("file")
    p.add_argument("--reason", required=True)
    p.set_defaults(fn=cmd_idea_drop)

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
    args.fn(args)


if __name__ == "__main__":
    main()
