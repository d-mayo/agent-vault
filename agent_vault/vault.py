"""The one way to create, move, and check notes in Agent/.

Usage: <CLI> <command>, where <CLI> is the exact command in the vault's
CLAUDE.md (the installer's Python plus this file). Commands:
  new daily [--date YYYY-MM-DD]
  new project <id> --title "Title" [--goal "..."] [--repo URL-or-path]
  log "what happened" [--project <id>]
  handoff list
  validate
  status
  schema
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # keep __pycache__ out of iCloud
import lib  # noqa: E402
from lib import AGENT, FOLDERS, VAULT


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def require_project(index: lib.Index, pid: str | None) -> None:
    if pid and pid not in index.projects:
        known = ", ".join(sorted(index.projects)) or "(none yet)"
        die(f"project '{pid}' does not exist. Known projects: {known}")


# --- new ---------------------------------------------------------------------
def create_daily(d: dt.date | None = None) -> tuple[Path, bool]:
    d = d or lib.today()
    path = lib.daily_path(d)
    if path.exists():
        return path, False
    fm = lib.render_frontmatter({"type": "daily", "date": d.isoformat()})
    title = f"{d.strftime('%A, %B')} {d.day}, {d.year}"
    lib.write_text(path, f"{fm}# {title}\n\n{lib.LOG_HEADING}\n")
    return path, True


def cmd_new(args) -> None:
    index = lib.Index()
    d = lib.today().isoformat()
    if args.kind == "daily":
        day = dt.date.fromisoformat(args.date) if args.date else None
        path, made = create_daily(day)
        print(("created " if made else "exists ") + lib.rel(path))
        return

    if args.kind == "project":
        pid = args.name
        if not lib.ID_RE.match(pid):
            die("project id must be lowercase-kebab-case, e.g. garden-redesign")
        if pid in index.projects:
            die(f"project '{pid}' already exists: {lib.rel(index.projects[pid])}")
        fm = {"type": "project", "id": pid, "status": "active", "created": d, "updated": d}
        if args.repo:
            fm["repo"] = args.repo
        body = (
            f"# {args.title or pid}\n\n"
            f"## Goal\n{args.goal or ''}\n\n"
            "## Current state\n\n"
            "## Next actions\n- [ ] \n\n"
            "## Decisions\n"
        )
        path = AGENT / FOLDERS["project"] / f"{pid}.md"
        lib.write_text(path, lib.render_frontmatter(fm) + body)
        print("created " + lib.rel(path))
        return


# --- log ---------------------------------------------------------------------
def cmd_log(args) -> None:
    index = lib.Index()
    require_project(index, args.project)
    path, _ = create_daily()
    text = lib.read_text(path).rstrip("\n")
    entry = f"- {lib.now_hhmm()} — {args.text.strip()}"
    if args.project:
        entry += f" [[{args.project}]]"
    lib.write_text(path, text + "\n" + entry + "\n")
    print(f"logged to {lib.rel(path)}: {entry}")


# --- bookkeeping ---------------------------------------------------------------
def set_fields(path: Path, **fields) -> None:
    fm, body, _ = lib.split_frontmatter(lib.read_text(path))
    fm = fm or {}
    fm.update(fields)
    lib.write_text(path, lib.render_frontmatter(fm) + body.lstrip("\n"))


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


def cmd_handoff(_args) -> None:
    done = load_handoffs()
    pending = [p for p in phone_notes() if lib.rel(p) not in done]
    if not pending:
        print("no phone-side notes waiting")
    for p in pending[:25]:
        when = dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d")
        print(f"{when}  {lib.rel(p)}")
    if len(pending) > 25:
        print(f"... and {len(pending) - 25} more")


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


def goal_line(path: Path) -> str:
    _, body, _ = lib.split_frontmatter(lib.read_text(path))
    lines = body.splitlines()
    for i, l in enumerate(lines):
        if l.strip() in ("## Purpose", "## Goal"):
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
            groups[st].append(f"- [[{pid}]] — {goal_line(path)} (updated {fm.get('updated') or fm.get('audited') or 'n/a'})")
    out = ["Active projects:"] + (groups["active"] or ["- (none)"])
    if groups["paused"]:
        out += ["Paused projects:"] + groups["paused"]
    return "\n".join(out)


def cmd_status(_args) -> None:
    print(status_text())


def cmd_schema(_args) -> None:
    print(lib.schema_text())


def main() -> None:
    lib.setup_io()
    if lib.VAULT is None:
        die(f"no usable vault: {lib.CONFIG} is missing, or its vault path has no .obsidian folder; "
            "run install.py --vault <path>")
    ap = argparse.ArgumentParser(prog="vault.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("new")
    p.add_argument("kind", choices=["daily", "project"])
    p.add_argument("name", nargs="?")
    p.add_argument("--date")
    p.add_argument("--title")
    p.add_argument("--goal")
    p.add_argument("--repo")
    p.add_argument("--project")
    p.set_defaults(fn=cmd_new)

    p = sub.add_parser("log")
    p.add_argument("text")
    p.add_argument("--project")
    p.set_defaults(fn=cmd_log)

    p = sub.add_parser("handoff")
    p.add_argument("action", choices=["list"])
    p.set_defaults(fn=cmd_handoff)

    for name, fn in (("validate", cmd_validate), ("status", cmd_status), ("schema", cmd_schema)):
        sub.add_parser(name).set_defaults(fn=fn)

    args = ap.parse_args()
    if args.cmd == "new" and args.kind != "daily" and not args.name:
        die(f"'new {args.kind}' needs a name")
    args.fn(args)


if __name__ == "__main__":
    main()
