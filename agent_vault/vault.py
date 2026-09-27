"""The one way to create, move, and check notes in Agent/.

Usage: <CLI> <command>, where <CLI> is the exact command in the vault's
CLAUDE.md (the installer's Python plus this file). Commands:
  new daily [--date YYYY-MM-DD]
  new project <id> --title "Title" [--goal "..."] [--repo URL-or-path]
  new note "Title" [--project <id>]
  log "what happened" [--project <id>]
  archive <project-id>
  handoff list
  handoff pull "<phone-side path>" [--project <id>]
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

    if args.kind == "note":
        require_project(index, args.project)
        title = lib.safe_title(args.name)
        path = AGENT / FOLDERS["note"] / f"{title}.md"
        if path.exists():
            die(f"{lib.rel(path)} already exists")
        fm = {"type": "note", "created": d}
        if args.project:
            fm["project"] = f"[[{args.project}]]"
        lib.write_text(path, lib.render_frontmatter(fm) + f"# {title}\n\n")
        print("created " + lib.rel(path))


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


# --- archive -------------------------------------------------------------------
def set_fields(path: Path, **fields) -> None:
    fm, body, _ = lib.split_frontmatter(lib.read_text(path))
    fm = fm or {}
    fm.update(fields)
    lib.write_text(path, lib.render_frontmatter(fm) + body.lstrip("\n"))


def cmd_archive(args) -> None:
    src = AGENT / FOLDERS["project"] / f"{args.id}.md"
    if not src.exists():
        die(f"no active project file {lib.rel(src)}")
    set_fields(src, status="done", updated=lib.today().isoformat())
    dest = AGENT / lib.ARCHIVE / src.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    src.replace(dest)
    print(f"archived to {lib.rel(dest)} (links by name still resolve)")


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


def cmd_handoff(args) -> None:
    done = load_handoffs()
    if args.action == "list":
        pending = [p for p in phone_notes() if lib.rel(p) not in done]
        if not pending:
            print("no phone-side notes waiting")
        for p in pending[:25]:
            when = dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d")
            print(f"{when}  {lib.rel(p)}")
        if len(pending) > 25:
            print(f"... and {len(pending) - 25} more")
        return

    src = Path(args.path)
    if not src.is_absolute():
        src = VAULT / src
    if not src.exists() or src.suffix.lower() != ".md":
        die(f"not a note: {args.path}")
    parts = lib.vault_parts(src)
    if parts is None or parts[0] == AGENT.name.lower():
        die("handoff only pulls notes from the phone side (outside Agent/)")
    index = lib.Index()
    require_project(index, args.project)

    d = lib.today().isoformat()
    link = lib.rel(src)[:-3]
    fm = {"type": "intake", "source": f"[[{link}]]", "status": "open", "created": d}
    if args.project:
        fm["project"] = f"[[{args.project}]]"
    dest = AGENT / FOLDERS["intake"] / f"{d}-{lib.slugify(src.stem)}.md"
    n = 2
    while dest.exists():
        dest = dest.with_name(f"{d}-{lib.slugify(src.stem)}-{n}.md")
        n += 1
    body = f"# {src.stem}\n\nFrom phone note [[{link}]].\n\n## What to do\n\n"
    lib.write_text(dest, lib.render_frontmatter(fm) + body)
    done[lib.rel(src)] = d
    lib.write_text(lib.HANDOFF_LOG, json.dumps(done, indent=2, sort_keys=True) + "\n")
    print("created " + lib.rel(dest))


# --- validate / status / schema ---------------------------------------------------------
def cmd_validate(_args) -> None:
    results = lib.validate_all()
    if not results:
        print("vault OK")
        return
    for path, probs in results.items():
        for p in probs:
            print(f"{path}: {p}")
    sys.exit(1)


def goal_line(path: Path) -> str:
    _, body, _ = lib.split_frontmatter(lib.read_text(path))
    lines = body.splitlines()
    for i, l in enumerate(lines):
        if l.strip() == "## Goal":
            for nxt in lines[i + 1:]:
                if nxt.startswith("## "):
                    break
                if nxt.strip():
                    return nxt.strip()[:100]
    return "(no goal written)"


def status_text() -> str:
    index = lib.Index()
    groups = {"active": [], "paused": []}
    for pid, path in sorted(index.projects.items()):
        fm, _, _ = lib.split_frontmatter(lib.read_text(path))
        st = (fm or {}).get("status")
        if st in groups:
            groups[st].append(f"- [[{pid}]] — {goal_line(path)} (updated {fm.get('updated')})")
    intake = []
    for p in sorted((AGENT / FOLDERS["intake"]).glob("*.md")):
        fm, _, _ = lib.split_frontmatter(lib.read_text(p))
        if (fm or {}).get("status") == "open":
            intake.append(f"- {lib.rel(p)}")
    out = ["Active projects:"] + (groups["active"] or ["- (none)"])
    if groups["paused"]:
        out += ["Paused projects:"] + groups["paused"]
    out += [f"Open intake items: {len(intake)}"] + intake
    return "\n".join(out)


def cmd_status(_args) -> None:
    print(status_text())


def cmd_schema(_args) -> None:
    print("Note types (folder -> required fields; optional fields):")
    for t, folder in FOLDERS.items():
        opt = lib.OPTIONAL[t] + lib.ALWAYS_ALLOWED
        print(f"  {t:8} Agent/{folder}/  required: {', '.join(lib.REQUIRED[t])}; optional: {', '.join(opt)}")
    print(f"  Archived projects move to Agent/{lib.ARCHIVE}/ (via `vault.py archive`).")
    for t, st in lib.STATUSES.items():
        print(f"Status values for {t}: {', '.join(st)}")
    print("Dates are YYYY-MM-DD. Project ids are lowercase-kebab-case and equal the filename.")
    print("Links to projects are quoted wikilinks: project: \"[[project-id]]\".")
    print(f"Project notes must have sections: {', '.join('## ' + s for s in lib.PROJECT_SECTIONS)}.")
    print(f"Daily notes: filename = date; entries under '{lib.LOG_HEADING}' as '- HH:MM — text [[project-id]]'.")
    print("Every [[link]] in Agent/ must resolve to an existing file.")


def main() -> None:
    lib.setup_io()
    if lib.VAULT is None:
        die(f"no usable vault: {lib.CONFIG} is missing, or its vault path has no .obsidian folder; "
            "run install.py --vault <path>")
    ap = argparse.ArgumentParser(prog="vault.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("new")
    p.add_argument("kind", choices=["daily", "project", "note"])
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

    p = sub.add_parser("archive")
    p.add_argument("id")
    p.set_defaults(fn=cmd_archive)

    p = sub.add_parser("handoff")
    p.add_argument("action", choices=["list", "pull"])
    p.add_argument("path", nargs="?")
    p.add_argument("--project")
    p.set_defaults(fn=cmd_handoff)

    for name, fn in (("validate", cmd_validate), ("status", cmd_status), ("schema", cmd_schema)):
        sub.add_parser(name).set_defaults(fn=fn)

    args = ap.parse_args()
    if args.cmd == "new" and args.kind != "daily" and not args.name:
        die(f"'new {args.kind}' needs a name")
    if args.cmd == "handoff" and args.action == "pull" and not args.path:
        die("'handoff pull' needs a path")
    args.fn(args)


if __name__ == "__main__":
    main()
