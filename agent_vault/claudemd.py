"""Parse and lint a repo's `CLAUDE.md` (design-v1.md §8).

Sections are the file's `##` headings. A section may carry a `covers` comment
right after its heading:

    <!-- covers: <glob>[, <glob>]; verified: YYYY-MM-DD -->

`lint()` checks paths, commands, covers globs, the line cap, and per-section
staleness from git history (a warning, never a failure — D2). Only backticked
strings in `## Commands` are treated as commands (D4); backticked strings
elsewhere are treated as paths unless excluded (AC2). Standard library only.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import fnmatch
import json
import os
import re
import shutil
from pathlib import Path

import github
import lib

LINE_CAP = 120
COVERS_RE = re.compile(
    r"^<!--\s*covers:\s*(?P<globs>[^;]+?)\s*(?:;\s*verified:\s*(?P<date>\S+))?\s*-->\s*$")
BACKTICK_RE = re.compile(r"`([^`\n]+)`")
DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")
KNOWN_EXTS = {
    "py", "pyi", "js", "mjs", "cjs", "ts", "tsx", "jsx", "json", "md", "txt",
    "yml", "yaml", "toml", "cfg", "ini", "sh", "bash", "ps1", "bat", "cmd",
    "css", "scss", "html", "xml", "csv", "lock", "env", "rb", "go", "rs",
    "java", "c", "h", "cpp", "hpp", "gradle", "kt", "swift", "php", "sql",
    "proto", "ipynb",
}
MAKE_NAMES = ("Makefile", "makefile", "GNUmakefile")
JUST_NAMES = ("justfile", "Justfile", ".justfile")


@dataclasses.dataclass
class Section:
    name: str
    start: int              # 1-indexed line number of the '## heading'
    end: int                # last line number belonging to this section
    covers: list           # globs; [] if no covers comment
    verified: object        # dt.date | None
    malformed: str = None   # set if a '<!-- covers' comment couldn't be parsed


@dataclasses.dataclass
class LintResult:
    errors: list
    warnings: list
    stale: int               # number of stale sections (also counted in warnings)


def parse(text: str) -> tuple:
    """(sections, malformed-comment problems), in heading order."""
    lines = text.splitlines()
    heads = [(i, ln[3:].strip()) for i, ln in enumerate(lines, 1) if ln.startswith("## ")]
    sections, problems = [], []
    for idx, (start, name) in enumerate(heads):
        end = heads[idx + 1][0] - 1 if idx + 1 < len(heads) else len(lines)
        covers, verified, malformed = [], None, None
        body_first = lines[start] if start < len(lines) else ""
        if body_first.strip().startswith("<!--"):
            m = COVERS_RE.match(body_first.strip())
            if not m:
                malformed = f"'## {name}' has a malformed covers comment: {body_first.strip()!r}"
            else:
                covers = [g.strip() for g in m.group("globs").split(",") if g.strip()]
                date_str = m.group("date")
                if date_str:
                    if lib.DATE_RE.match(date_str):
                        verified = dt.date.fromisoformat(date_str)
                    else:
                        malformed = f"'## {name}' has a bad verified date: {date_str!r}"
        sections.append(Section(name, start, end, covers, verified, malformed))
        if malformed:
            problems.append(malformed)
    return sections, problems


def section_at(sections: list, lineno: int):
    for s in sections:
        if s.start <= lineno <= s.end:
            return s
    return None


# --- paths ---------------------------------------------------------------------
def looks_like_path(tok: str) -> bool:
    if not tok or any(c.isspace() for c in tok):
        return False
    if tok.startswith("~") or tok.startswith(("/", "\\")) or DRIVE_RE.match(tok):
        return False
    if "<" in tok or ">" in tok:
        return False
    if any(c in "*?[]" for c in tok):
        return False
    if "/" in tok or "\\" in tok:
        return True
    if tok.startswith("."):
        return True
    ext = tok.rsplit(".", 1)[-1].lower() if "." in tok else ""
    return ext in KNOWN_EXTS


def check_paths(repo: Path, sections: list, lines: list) -> list:
    errors = []
    for lineno, line in enumerate(lines, 1):
        sec = section_at(sections, lineno)
        if sec and sec.name == "Commands":
            continue
        for tok in BACKTICK_RE.findall(line):
            tok = tok.strip()
            if looks_like_path(tok) and not (repo / tok).exists():
                errors.append(f"line {lineno}: path '{tok}' does not exist")
    return errors


# --- commands (D4) --------------------------------------------------------------
def npm_script_exists(repo: Path, name: str) -> bool:
    pkg = repo / "package.json"
    if not pkg.is_file():
        return False
    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return name in (data.get("scripts") or {})


def target_exists(repo: Path, filenames: tuple, name: str) -> bool:
    target_re = re.compile(r"^([A-Za-z0-9_.\-%]+)\s*:(?!=)")
    for fname in filenames:
        f = repo / fname
        if not f.is_file():
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for ln in text.splitlines():
            m = target_re.match(ln)
            if m and m.group(1) == name:
                return True
    return False


def resolve_command(repo: Path, cmd: str) -> bool:
    """True if `cmd`'s first word (or npm run/make/just plus target) resolves.
    A command whose first word is a placeholder is treated as unresolvable-but-
    not-an-error (skipped) by the caller, never reaching here."""
    parts = cmd.split()
    if not parts:
        return True
    first = parts[0]
    if first == "npm" and len(parts) >= 3 and parts[1] == "run":
        return npm_script_exists(repo, parts[2])
    if first == "make" and len(parts) >= 2:
        return target_exists(repo, MAKE_NAMES, parts[1])
    if first == "just" and len(parts) >= 2:
        return target_exists(repo, JUST_NAMES, parts[1])
    if "/" in first or "\\" in first:
        return (repo / first).is_file()
    return shutil.which(first) is not None


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


# --- covers globs and the line cap ----------------------------------------------
def all_repo_files(repo: Path):
    """Repo-relative, forward-slash paths of every tracked-looking file (skips .git)."""
    for dirpath, dirnames, filenames in os.walk(repo):
        if ".git" in dirnames:
            dirnames.remove(".git")
        for fn in filenames:
            yield (Path(dirpath) / fn).relative_to(repo).as_posix()


def glob_matches_any_file(repo: Path, pattern: str, files: list) -> bool:
    pat = pattern.replace("\\", "/")
    return any(fnmatch.fnmatchcase(f, pat) for f in files)


def check_globs(repo: Path, sections: list) -> list:
    errors = []
    if not any(sec.covers for sec in sections):
        return errors
    files = list(all_repo_files(repo))
    for sec in sections:
        for g in sec.covers:
            if not glob_matches_any_file(repo, g, files):
                errors.append(f"'## {sec.name}' covers glob '{g}' matches no file")
    return errors


def check_cap(lines: list) -> list:
    return [f"{len(lines)} lines; the cap is {LINE_CAP}"] if len(lines) > LINE_CAP else []


# --- staleness (warning only, D2) -----------------------------------------------
def blame_last_changed(repo: Path, relpath: str, start: int, end: int):
    """Calendar date of the newest commit that touched lines [start, end] of
    `relpath`, or None if it can't be determined (uncommitted, no history)."""
    out = github.git("blame", "--porcelain", "-L", f"{start},{end}", "--", relpath,
                     cwd=repo, check=False)
    times, cur_sha = {}, None
    sha_re = re.compile(r"^([0-9a-f]{40}) \d+ \d+")
    for ln in out.splitlines():
        m = sha_re.match(ln)
        if m:
            cur_sha = m.group(1)
            continue
        if cur_sha and ln.startswith("committer-time "):
            if cur_sha != "0" * 40:
                times[cur_sha] = int(ln.split()[1])
    if not times:
        return None
    return dt.date.fromtimestamp(max(times.values()))


def newest_commit_for_globs(repo: Path, globs: list):
    """(short sha, calendar date) of the newest commit touching any covered
    glob, or None if there's no history for them."""
    pathspecs = [f":(glob){g}" for g in globs]
    out = github.git("log", "-1", "--format=%h|%ct", "--", *pathspecs, cwd=repo, check=False)
    if not out or "|" not in out:
        return None
    short, ts = out.split("|", 1)
    try:
        return short, dt.date.fromtimestamp(int(ts))
    except ValueError:
        return None


def check_staleness(repo: Path, relpath: str, sections: list) -> list:
    warnings = []
    for sec in sections:
        if not sec.covers:
            continue                                   # D5: no covers, never stale
        last_changed = blame_last_changed(repo, relpath, sec.start, sec.end)
        cutoff = max(d for d in (last_changed, sec.verified) if d) \
            if (last_changed or sec.verified) else None
        if cutoff is None:
            continue
        newest = newest_commit_for_globs(repo, sec.covers)
        if newest and newest[1] > cutoff:
            warnings.append(f"'## {sec.name}' looks stale: {newest[0]} ({newest[1]}) "
                            f"touched a covered file after {cutoff}")
    return warnings


# --- seal retro's verified-date bump (design §5 step 4, §7.4) ------------------
def render_covers(covers: list, verified) -> str:
    out = f"<!-- covers: {', '.join(covers)}"
    if verified:
        out += f"; verified: {verified.isoformat()}"
    return out + " -->"


def bump_verified(text: str, sections: list, confirmed: set, today: dt.date) -> tuple:
    """Set `verified: today` on every section in `confirmed` that has a covers
    comment (D5: nothing to bump without one). Returns (new text, headings bumped)."""
    lines = text.splitlines()
    bumped = []
    for sec in sections:
        if sec.name not in confirmed or not sec.covers or sec.malformed:
            continue
        lines[sec.start] = render_covers(sec.covers, today)
        bumped.append(sec.name)
    if not bumped:
        return text, bumped
    new_text = "\n".join(lines)
    return (new_text + "\n" if text.endswith("\n") else new_text), bumped


# --- entry point -----------------------------------------------------------------
def lint(repo: Path) -> LintResult:
    path = repo / "CLAUDE.md"
    errors, warnings = [], []
    if not path.is_file():
        return LintResult([f"{path} does not exist"], [], 0)
    text = lib.read_text(path)
    sections, malformed = parse(text)
    errors += [f"CLAUDE.md: {m}" for m in malformed]
    lines = lib.blank_code_blocks(text)
    errors += [f"CLAUDE.md: {e}" for e in check_paths(repo, sections, lines)]
    errors += [f"CLAUDE.md: {e}" for e in check_commands(repo, sections, lines)]
    errors += [f"CLAUDE.md: {e}" for e in check_globs(repo, sections)]
    errors += [f"CLAUDE.md: {e}" for e in check_cap(text.splitlines())]
    stale = check_staleness(repo, "CLAUDE.md", sections)
    warnings += [f"CLAUDE.md: {w}" for w in stale]
    return LintResult(errors, warnings, len(stale))
