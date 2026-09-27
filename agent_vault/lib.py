"""Shared rules and helpers for the Agent/ workflow system.

Everything that defines "a valid note" lives here, in one place. The CLI
(vault.py), the hooks, and the `vault.py schema` output all read from these
constants, so the rules can't drift apart. Standard library only.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import tempfile
from pathlib import Path

# --- Locations ---------------------------------------------------------------
# The code is installed outside the vault (~/.claude/agent-vault/), so the vault
# location comes from the config the installer writes. AGENT_VAULT_CONFIG
# overrides the config path (used by tests).
INSTALL_DIR = Path(__file__).resolve().parent
CONFIG = Path(os.environ.get("AGENT_VAULT_CONFIG")
              or Path.home() / ".claude" / "agent-vault.json")
# The interpreter running this code is the one the installer wrote into the hooks.
CLI = f'"{Path(sys.executable).as_posix()}" "{(INSTALL_DIR / "vault.py").as_posix()}"'


def _load_vault():
    """The configured vault, or None if unconfigured or not (yet) an Obsidian
    vault, e.g. moved or not synced. Never guess: a wrong path would get notes."""
    try:
        path = Path(json.loads(CONFIG.read_text(encoding="utf-8-sig"))["vault"])
    except Exception:
        return None
    return path if (path / ".obsidian").is_dir() else None


VAULT = _load_vault()                      # vault root (Notes), or None
AGENT = VAULT / "Agent" if VAULT else None

FOLDERS = {                                # note type -> folder
    "daily": "Daily",
    "project": "Projects",
    "note": "Notes",
    "intake": "Intake",
}
ARCHIVE = "Archive"
TYPE_BY_FOLDER = {v.lower(): k for k, v in FOLDERS.items()}
HANDOFF_LOG = AGENT / ".handoff-log.json" if AGENT else None

# --- Schema ------------------------------------------------------------------
REQUIRED = {
    "daily": ["type", "date"],
    "project": ["type", "id", "status", "created", "updated"],
    "note": ["type", "created"],
    "intake": ["type", "source", "status", "created"],
}
OPTIONAL = {
    "daily": [],
    "project": ["repo"],
    "note": ["project"],
    "intake": ["project"],
}
ALWAYS_ALLOWED = ["tags", "aliases"]
STATUSES = {
    "project": ["active", "paused", "done"],
    "intake": ["open", "done"],
}
PROJECT_SECTIONS = ["Goal", "Current state", "Next actions", "Decisions"]
DATE_FIELDS = ["date", "created", "updated"]
LOG_HEADING = "## Log"

ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LOG_LINE_RE = re.compile(r"^- \d{2}:\d{2} — \S")
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")


def setup_io() -> None:
    """Force UTF-8 on Windows pipes so titles/em dashes never crash a hook."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


def today() -> dt.date:
    return dt.date.today()


def now_hhmm() -> str:
    return dt.datetime.now().strftime("%H:%M")


# --- Frontmatter ---------------------------------------------------------------
def split_frontmatter(text: str):
    """Return (dict | None, body, errors)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, text, []
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            data, errors = _parse_fm(lines[1:i])
            return data, "\n".join(lines[i + 1:]), errors
    return None, text, ["frontmatter is never closed with '---'"]


def _parse_fm(lines):
    data, errors = {}, []
    for ln in lines:
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        if ln.startswith((" ", "\t", "-")):
            errors.append(f"multi-line YAML isn't supported here: {ln.strip()!r}")
            continue
        if ":" not in ln:
            errors.append(f"not a 'key: value' line: {ln.strip()!r}")
            continue
        key, val = ln.split(":", 1)
        key, val = key.strip(), val.strip()
        if val.startswith("[["):
            errors.append(f"'{key}' wikilink must be quoted, e.g. {key}: \"{val}\"")
        elif len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        elif val.startswith("[") and val.endswith("]"):
            val = [x.strip().strip("\"'") for x in val[1:-1].split(",") if x.strip()]
        data[key] = val
    return data, errors


def render_frontmatter(data: dict) -> str:
    out = ["---"]
    for k, v in data.items():
        if isinstance(v, list):
            out.append(f"{k}: [{', '.join(v)}]")
        elif isinstance(v, str) and (v[:1] in "[{#*&!|>'\"%@`" or ": " in v or "://" in v):
            out.append(f'{k}: "{v}"')
        else:
            out.append(f"{k}: {v}")
    out.append("---")
    return "\n".join(out) + "\n"


def read_text(path: Path) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


# --- Paths -------------------------------------------------------------------
def vault_parts(path, base=None):
    """Lower-cased path parts relative to the vault, or None if outside it."""
    p = Path(path)
    if not p.is_absolute():
        p = Path(base or os.getcwd()) / p
    ap = os.path.normcase(os.path.realpath(p))
    vp = os.path.normcase(os.path.realpath(VAULT))
    try:
        if os.path.commonpath([ap, vp]) != vp:
            return None
    except ValueError:          # different drives on Windows
        return None
    rel = os.path.relpath(ap, vp)
    return [] if rel == "." else [x.lower() for x in Path(rel).parts]


def rel(path: Path) -> str:
    return path.relative_to(VAULT).as_posix()


def is_hidden(path: Path) -> bool:
    return any(part.startswith(".") for part in path.relative_to(VAULT).parts)


def daily_path(d: dt.date | None = None) -> Path:
    return AGENT / FOLDERS["daily"] / f"{(d or today()).isoformat()}.md"


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "untitled"


def safe_title(text: str) -> str:
    return re.sub(r'[\\/:*?"<>|#^\[\]]', "", text).strip() or "Untitled"


# --- Vault index ---------------------------------------------------------------
class Index:
    """Everything a link or project reference could resolve to."""

    def __init__(self):
        self.md_stems, self.names = set(), set()
        for p in VAULT.rglob("*"):
            if not p.is_file() or is_hidden(p):
                continue
            self.names.add(p.name.lower())
            if p.suffix.lower() == ".md":
                self.md_stems.add(p.stem.lower())
        self.projects = {}   # id -> Path
        for folder in (FOLDERS["project"], ARCHIVE):
            for p in (AGENT / folder).glob("*.md"):
                fm, _, _ = split_frontmatter(read_text(p))
                if fm and fm.get("type") == "project":
                    self.projects[p.stem] = p

    def resolves(self, target: str) -> bool:
        name = target.strip().replace("\\", "/").split("/")[-1].lower()
        return name in self.md_stems or name in self.names


def project_ref(value) -> str | None:
    """'[[my-project]]' -> 'my-project'; anything else -> None."""
    if isinstance(value, str):
        m = re.fullmatch(r"\[\[([^\]|#]+)\]\]", value.strip())
        if m:
            return m.group(1).strip()
    return None


# --- Validation ----------------------------------------------------------------
def expected_type(path: Path) -> str | None:
    parts = path.relative_to(AGENT).parts
    if len(parts) < 2:
        return None
    return TYPE_BY_FOLDER.get(parts[0].lower())


def validate_file(path: Path, index: Index | None = None) -> list[str]:
    index = index or Index()
    parts = path.relative_to(AGENT).parts
    top = parts[0].lower() if parts else ""
    if top == "_system":
        return []
    if len(parts) < 2 or (top not in TYPE_BY_FOLDER and top != ARCHIVE.lower()):
        return [f"stray file: notes belong in {', '.join(list(FOLDERS.values()) + [ARCHIVE])}"]

    text = read_text(path)
    fm, body, errors = split_frontmatter(text)
    problems = list(errors)
    if fm is None:
        return problems + ["missing frontmatter"]

    ntype = fm.get("type")
    want = expected_type(path)
    if ntype not in REQUIRED:
        return problems + [f"type must be one of {sorted(REQUIRED)} (got {ntype!r})"]
    if want and ntype != want:
        problems.append(f"type is {ntype!r} but notes in {parts[0]}/ must be {want!r}")

    for key in REQUIRED[ntype]:
        if not fm.get(key):
            problems.append(f"missing required field '{key}'")
    allowed = set(REQUIRED[ntype]) | set(OPTIONAL[ntype]) | set(ALWAYS_ALLOWED)
    for key in fm:
        if key not in allowed:
            problems.append(f"unknown field '{key}' for type {ntype}")
    for key in DATE_FIELDS:
        if key in fm and not (isinstance(fm[key], str) and DATE_RE.match(fm[key])):
            problems.append(f"'{key}' must be YYYY-MM-DD")
    if ntype in STATUSES and fm.get("status") and fm["status"] not in STATUSES[ntype]:
        problems.append(f"status must be one of {STATUSES[ntype]}")

    if "project" in fm and ntype != "project":
        pid = project_ref(fm["project"])
        if pid is None:
            problems.append("'project' must be a single link like \"[[project-id]]\"")
        elif pid not in index.projects:
            problems.append(f"project [[{pid}]] does not exist (see Agent/Projects)")

    if ntype == "daily":
        if fm.get("date") != path.stem:
            problems.append("daily note filename must equal its date")
        problems += _check_log(body)
    elif ntype == "project":
        if fm.get("id") != path.stem:
            problems.append("project 'id' must equal its filename")
        if fm.get("id") and not ID_RE.match(fm["id"]):
            problems.append("project id must be lowercase-kebab-case")
        headings = {h.strip() for h in re.findall(r"^## (.+)$", body, re.M)}
        for sec in PROJECT_SECTIONS:
            if sec not in headings:
                problems.append(f"missing section '## {sec}'")
    elif ntype == "intake":
        src = project_ref(fm.get("source"))
        if src is None or not index.resolves(src):
            problems.append("'source' must link to an existing phone-side note")

    for target in WIKILINK_RE.findall(body):
        if not index.resolves(target):
            problems.append(f"broken link [[{target}]]")
    return problems


def _check_log(body: str) -> list[str]:
    lines = body.splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.strip() == LOG_HEADING)
    except StopIteration:
        return [f"missing '{LOG_HEADING}' section"]
    problems = []
    for ln in lines[start + 1:]:
        if ln.startswith("## "):
            break
        if not ln.strip() or ln.startswith(("  ", "\t")):
            continue
        if not LOG_LINE_RE.match(ln):
            problems.append(f"log line must look like '- HH:MM — text': {ln[:60]!r}")
    return problems


def validate_all() -> dict[str, list[str]]:
    index = Index()
    results = {}
    for p in sorted(AGENT.rglob("*.md")):
        if is_hidden(p):
            continue
        probs = validate_file(p, index)
        if probs:
            results[rel(p)] = probs
    return results


# --- Hook session state -----------------------------------------------------------
STATE_DIR = Path(tempfile.gettempdir()) / "obsidian-agent-hooks"


def _state_file(session_id: str) -> Path:
    return STATE_DIR / (re.sub(r"[^A-Za-z0-9_-]", "_", session_id or "unknown") + ".json")


def load_state(session_id: str) -> dict:
    try:
        return json.loads(_state_file(session_id).read_text(encoding="utf-8"))
    except Exception:
        return {"touched": {}}


def save_state(session_id: str, state: dict) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        _state_file(session_id).write_text(json.dumps(state), encoding="utf-8")
    except Exception:
        pass


def read_hook_input() -> dict:
    try:
        raw = sys.stdin.buffer.read().decode("utf-8")
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}
