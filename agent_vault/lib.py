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

HANDOFF_LOG = AGENT / ".handoff-log.json" if AGENT else None

# --- Schema (design-v1.md §6-7) ----------------------------------------------
TYPES = ["project", "plan", "impl", "retro", "idea", "daily"]
FOLDERS = {                                # folder key -> folder under Agent/
    "project": "Projects",
    "work": "Work",
    "idea": "Ideas",
    "daily": "Daily",
}
PATHS = {                                  # note type -> layout (for `schema`)
    "project": "Projects/<id>.md",
    "plan": "Work/<repo>/<repo>-<n>-plan.md",
    "impl": "Work/<repo>/<repo>-<n>-impl.md",
    "retro": "Work/<repo>/<repo>-<n>-retro.md",
    "idea": "Ideas/<YYYY-MM-DD>-<slug>.md",
    "daily": "Daily/<YYYY-MM-DD>.md",
}
REQUIRED = {
    "project": ["type", "id", "status", "repos"],
    "plan": ["type", "repo", "issue", "status"],
    "impl": ["type", "repo", "issue", "status"],
    "retro": ["type", "repo", "issue", "pr", "status"],
    "idea": ["type", "status", "created", "source"],
    "daily": ["type", "date"],
}
OPTIONAL = {
    "project": ["audited"],
    "plan": ["issue_updated", "base_sha"],
    "impl": ["branch", "pr"],
    "retro": [],
    "idea": ["project", "promoted_to"],
    "daily": [],
}
CONDITIONAL = {                            # shown by `schema`; enforced by FM-COND / PLAN-SEALED
    "plan": "issue_updated and base_sha are required when status is sealed",
    "idea": "promoted_to is required if and only if status is promoted",
}
ALWAYS_ALLOWED = ["tags", "aliases"]
STATUSES = {
    "project": ["active", "paused", "archived"],
    "plan": ["draft", "sealed"],
    "impl": ["open", "sealed"],
    "retro": ["open", "sealed"],
    "idea": ["open", "promoted", "dropped"],
}
DATE_FIELDS = {"project": ["audited"], "idea": ["created"], "daily": ["date"]}
TIMESTAMP_FIELDS = {"plan": ["issue_updated"]}
SECTIONS = {                               # required `##` sections, in order
    "project": ["Purpose", "Current state", "Architecture", "Standing decisions"],
    "plan": ["Goal", "Acceptance criteria", "Decisions", "Implementer's discretion",
             "Context", "Steps", "Tests", "Stop and ask if", "Out of scope"],
    "impl": ["Deviations", "Review findings", "Verification", "Discoveries"],
    "retro": ["Summary", "CLAUDE.md audit", "Overview audit", "Follow-ups"],
    "idea": [],
    "daily": ["Log"],
}
SIZE_CAPS = {"project": 60, "impl": 80, "retro": 40, "idea": 8}   # body lines; errors
PLAN_WARN_LINES = 400                                            # body lines; warning
EVENTS = ["planned", "started", "pr-opened", "retro-done"]          # '<repo>#<n> <event>' lines
IDEA_EVENTS = ["idea-added", "idea-promoted"]                       # '<event> — <title> [[<idea>]]' lines
# Completeness rules that only warn while a plan is a draft (errors once sealed).
DRAFT_SOFT = ["PLAN-AC", "PLAN-STEP", "PLAN-T", "PLAN-COVER", "PLAN-FULL"]
BRANCH_TYPES = ["feat", "fix", "chore", "docs", "refactor", "test", "perf", "hotfix"]   # design §4
BRANCH_RE = re.compile(rf"^(?:{'|'.join(BRANCH_TYPES)})/[0-9]+-[a-z0-9-]+$")
PLANNED_LABEL = ("planned", "0e8a16", "A sealed plan exists (set by agent-vault)")   # name, color, description
HOOK_MARKER = "agent-vault-hook: pre-push"        # in githooks/pre-push; repo-init recognises the hook by it
LOG_HEADING = "## Log"
NONE_LINE = "None"

ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
NUMBER_RE = re.compile(r"^[1-9]\d*$")
SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
REPO_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
REPO_SLUG_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
IDEA_FILE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z0-9]+(?:-[a-z0-9]+)*$")
WORK_FILE_RE = re.compile(r"^(?P<repo>[A-Za-z0-9._-]+?)-(?P<n>[1-9]\d*)-(?P<kind>plan|impl|retro)$")
IDEA_SOURCE_RE = re.compile(r"^(?:retro [A-Za-z0-9._-]+#[1-9]\d*|phone:\S.*|session)$")
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
STEP_HEADING_RE = re.compile(r"^### [1-9]\d*\. \S")
AC_LINE_RE = re.compile(r"^- AC(\d+): \S")
TEST_LINE_RE = re.compile(r"^- T\d+ → (AC\d+(?:, AC\d+)*): \S")
_TIME = r"(?:[01]\d|2[0-3]):[0-5]\d"

# Line formats: rule -> [(shape shown by `schema`, regex)]. A line under a section
# bound to the rule (SECTION_LINES) must match one of them.
LINE_FORMATS = {
    "PLAN-AC": [("- AC<n>: <criterion>", AC_LINE_RE)],
    "PLAN-D": [("- D<n>: <decision>, because <reason>", re.compile(r"^- D\d+: .+, because \S"))],
    "PLAN-T": [("- T<n> → AC<m>[, AC<k>]: <test>", TEST_LINE_RE),
               ("Full check: <command>", re.compile(r"^Full check: \S"))],
    "IMPL-DEV": [("- Step <n>: <deviation>", re.compile(r"^- Step [1-9]\d*: \S"))],
    "IMPL-REV": [("- R<n> (major|minor): <finding> → fixed in <sha>",
                  re.compile(r"^- R\d+ \((?:major|minor)\): .+ → fixed in [0-9a-f]{7,40}$")),
                 ("- R<n> (major|minor): <finding> → won't fix: <reason>",
                  re.compile(r"^- R\d+ \((?:major|minor)\): .+ → won't fix: \S"))],
    "RETRO-AUDIT": [("- <section heading>: confirmed|rewritten|removed — <reason>",
                     re.compile(r"^- .+: (?:confirmed|rewritten|removed) — \S"))],
    "RETRO-FOLLOW": [("- issue #<n> created", re.compile(r"^- issue #[1-9]\d* created$")),
                     ("- issue #<n> amended", re.compile(r"^- issue #[1-9]\d* amended$")),
                     ("- idea [[<idea>]]", re.compile(r"^- idea \[\[[^\]]+\]\]$")),
                     ("- dropped: <why>", re.compile(r"^- dropped: \S"))],
    "DAILY-LINE": [
        ("- HH:MM <repo>#<n> <event>[ — <detail>]",
         re.compile(rf"^- {_TIME} [A-Za-z0-9._-]+#[1-9]\d* (?:{'|'.join(EVENTS)})(?: — \S.*)?$")),
        ("- HH:MM session — <summary>[ [[<project>]]]",
         re.compile(rf"^- {_TIME} session — \S.*?(?: \[\[[^\]]+\]\])?$")),
        ("- HH:MM idea-added|idea-promoted — <title> [[<idea>]]",
         re.compile(rf"^- {_TIME} (?:{'|'.join(IDEA_EVENTS)}) — \S.*? \[\[[^\]]+\]\]$")),
    ],
}
# note type -> section -> (rule, "None" is a valid line)
SECTION_LINES = {
    "plan": {"Acceptance criteria": ("PLAN-AC", False), "Decisions": ("PLAN-D", False),
             "Tests": ("PLAN-T", False)},
    "impl": {"Deviations": ("IMPL-DEV", True), "Review findings": ("IMPL-REV", True)},
    "retro": {"CLAUDE.md audit": ("RETRO-AUDIT", False), "Overview audit": ("RETRO-AUDIT", False),
              "Follow-ups": ("RETRO-FOLLOW", True)},
    "daily": {"Log": ("DAILY-LINE", False)},
}

# Every validator rule: id -> (severity, what it checks). Problem messages carry
# the id; `schema` prints this table; tests/test_schema.py needs a passing and a
# failing fixture for each id.
RULES = {
    "PATH-STRAY": ("error", "Files in Agent/ must sit at a layout path (see Layout); anything else is stray"),
    "PATH-NAME": ("error", "The file name must match the pattern for its folder"),
    "FM-PARSE": ("error", "Frontmatter must be simple 'key: value' YAML; wikilinks must be quoted"),
    "FM-MISSING": ("error", "Every note starts with a frontmatter block"),
    "FM-TYPE": ("error", "'type' must be a known note type and match the note's path"),
    "FM-REQUIRED": ("error", "Required frontmatter fields must be present and non-empty"),
    "FM-UNKNOWN": ("error", "Unknown frontmatter fields are rejected"),
    "FM-ENUM": ("error", "'status' must be one of the values allowed for the type"),
    "FM-DATE": ("error", "Date fields are YYYY-MM-DD"),
    "FM-TS": ("error", "Timestamps are ISO 8601 UTC, e.g. 2026-09-26T21:04:00Z"),
    "FM-FORMAT": ("error", "Field values have the right shape (ids, repo names, numbers, sha, source)"),
    "FM-MATCH": ("error", "id, date, repo and issue must agree with the file name"),
    "FM-COND": ("error", "promoted_to is set if and only if an idea is promoted"),
    "SEC-MISSING": ("error", "Every required '##' section must be present"),
    "SEC-ORDER": ("error", "'##' sections must appear in the specified order"),
    "SEC-EXTRA": ("error", "Other (or duplicate) '##' sections are rejected"),
    "SIZE-CAP": ("error", "Body line caps: project 60, impl 80, retro 40, idea 8"),
    "SIZE-WARN": ("warning", "A plan over 400 body lines should be split into several issues"),
    "PLAN-AC": ("error", "Acceptance criteria are '- AC<n>: …' lines, numbers unique; at least one (warning while draft)"),
    "PLAN-D": ("error", "Decisions are '- D<n>: <decision>, because <reason>' lines"),
    "PLAN-STEP": ("error", "Each '### <n>. <title>' step has 'Files:' and 'Done when:'; at least one step (warning while draft)"),
    "PLAN-T": ("error", "Tests are '- T<n> → AC<m>[, AC<k>]: …' lines; at least one (warning while draft)"),
    "PLAN-COVER": ("error", "Every acceptance criterion is referenced by at least one test (warning while draft)"),
    "PLAN-REF": ("error", "Every AC a test references must exist"),
    "PLAN-FULL": ("error", "The Tests section has a 'Full check:' line (warning while draft)"),
    "PLAN-SEALED": ("error", "A sealed plan has issue_updated and base_sha"),
    "IMPL-DEV": ("error", "Deviation lines are '- Step <n>: …' (or None)"),
    "IMPL-REV": ("error", "Review findings end in '→ fixed in <sha>' or '→ won't fix: <reason>' (or None)"),
    "RETRO-AUDIT": ("error", "Audit lines are '- <heading>: confirmed|rewritten|removed — <reason>'"),
    "RETRO-FOLLOW": ("error", "Follow-up lines are issue created/amended, idea link or dropped"),
    "DAILY-LINE": ("error", "Log lines are '- HH:MM <repo>#<n> <event>[ — detail]', '- HH:MM session — …' or an idea event line"),
    "X-REPO-DUP": ("error", "A repo name appears in the repos: of at most one project"),
    "X-REPO-UNREG": ("error", "Work/<repo>/ belongs to a repo listed in some project's repos:"),
    "X-PROJECT": ("error", "An idea's 'project:' names an existing project"),
    "X-LINK": ("error", "Every [[link]] in Agent/ resolves (links in code are ignored)"),
}

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
    """Everything a link, project or repo reference could resolve to."""

    def __init__(self):
        self.md_stems, self.names = set(), set()
        for p in VAULT.rglob("*"):
            if not p.is_file() or is_hidden(p):
                continue
            self.names.add(p.name.lower())
            if p.suffix.lower() == ".md":
                self.md_stems.add(p.stem.lower())
        self.projects = {}   # id -> Path
        self.repos = {}      # repo name -> [project ids that list it in repos:]
        self.slugs = {}      # repo name -> owner/name, as written in repos:
        for p in sorted((AGENT / FOLDERS["project"]).glob("*.md")):
            fm, _, _ = split_frontmatter(read_text(p))
            if fm and fm.get("type") == "project":
                self.projects[p.stem] = p
                repos = fm.get("repos")
                for slug in repos if isinstance(repos, list) else []:
                    self.repos.setdefault(slug.split("/")[-1], []).append(p.stem)
                    self.slugs.setdefault(slug.split("/")[-1], slug)

    def resolves(self, target: str) -> bool:
        name = target.strip().replace("\\", "/").split("/")[-1].lower()
        return name in self.md_stems or name in self.names


# --- Validation ----------------------------------------------------------------
class Result:
    """Problems found in one note. Messages start with the rule id, e.g. '[SEC-ORDER] …'."""

    def __init__(self):
        self.errors, self.warnings = [], []

    def add(self, rule: str, msg: str, soft: bool = False) -> None:
        """soft: report as a warning although the rule is an error (draft plans, D1)."""
        is_error = RULES[rule][0] == "error" and not soft
        (self.errors if is_error else self.warnings).append(f"[{rule}] {msg}")


def blank_code_blocks(text: str) -> list[str]:
    """Lines of text with fenced code blocks emptied (line count is kept)."""
    out, fence = [], None
    for ln in text.splitlines():
        s = ln.lstrip()
        marker = s[:3] if s[:3] in ("```", "~~~") else None
        if fence:
            fence = None if marker == fence else fence
            out.append("")
        elif marker:
            fence = marker
            out.append("")
        else:
            out.append(ln)
    return out


def split_sections(lines: list[str]) -> list[tuple[str, list[str]]]:
    out = []
    for ln in lines:
        if ln.startswith("## "):
            out.append((ln[3:].strip(), []))
        elif out:
            out[-1][1].append(ln)
    return out


def classify(parts: tuple[str, ...]):
    """Where a file sits in the layout: (type | None, fields the name implies, problem | None)."""
    name = parts[-1]
    stem = name[:-3] if name.lower().endswith(".md") else None
    top = parts[0]
    depth = len(parts)
    stray = ("PATH-STRAY", f"Agent/{'/'.join(parts)} is not in the layout; "
             f"allowed: {', '.join(PATHS.values())}. Move or delete it")

    def bad_name(rule_hint: str):
        return None, {}, ("PATH-NAME", f"'{name}' must look like {rule_hint}")

    if stem is None:
        return None, {}, stray
    if top == FOLDERS["project"] and depth == 2:
        if not ID_RE.match(stem):
            return bad_name(PATHS["project"])
        return "project", {"id": stem}, None
    if top == FOLDERS["daily"] and depth == 2:
        if not DATE_RE.match(stem):
            return bad_name(PATHS["daily"])
        return "daily", {"date": stem}, None
    if top == FOLDERS["idea"] and depth == 2:
        if not IDEA_FILE_RE.match(stem):
            return bad_name(PATHS["idea"])
        return "idea", {}, None
    if top == FOLDERS["work"] and depth == 3:
        m = WORK_FILE_RE.match(stem)
        if not m or m.group("repo") != parts[1]:
            return bad_name(f"{parts[1]}-<n>-plan|impl|retro.md")
        return m.group("kind"), {"repo": m.group("repo"), "issue": m.group("n")}, None
    return None, {}, stray


def _check_frontmatter(res: Result, ntype: str, fm: dict, derived: dict) -> None:
    for key in REQUIRED[ntype]:
        if fm.get(key) in (None, ""):
            res.add("FM-REQUIRED", f"missing required field '{key}'")
    allowed = set(REQUIRED[ntype]) | set(OPTIONAL[ntype]) | set(ALWAYS_ALLOWED)
    for key in fm:
        if key not in allowed:
            res.add("FM-UNKNOWN", f"unknown field '{key}' for type {ntype}; remove it")
    for key, val in fm.items():
        if isinstance(val, list) and key not in ("repos", *ALWAYS_ALLOWED):
            res.add("FM-FORMAT", f"'{key}' must be a single value, not a list")

    status = fm.get("status")
    if ntype in STATUSES and status and status not in STATUSES[ntype]:
        res.add("FM-ENUM", f"status must be one of {STATUSES[ntype]} (got {status!r})")
    for key in DATE_FIELDS.get(ntype, []):
        val = fm.get(key)
        if val in (None, ""):
            continue
        try:
            ok = isinstance(val, str) and DATE_RE.match(val) and dt.date.fromisoformat(val)
        except ValueError:
            ok = False
        if not ok:
            res.add("FM-DATE", f"'{key}' must be a real date, YYYY-MM-DD (got {val!r})")
    for key in TIMESTAMP_FIELDS.get(ntype, []):
        val = fm.get(key)
        if val in (None, ""):
            continue
        try:
            ok = isinstance(val, str) and TIMESTAMP_RE.match(val) and dt.datetime.strptime(val, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            ok = False
        if not ok:
            res.add("FM-TS", f"'{key}' must be a UTC timestamp like 2026-09-26T21:04:00Z (got {val!r})")

    def scalar(key):
        val = fm.get(key)
        return val if isinstance(val, str) and val else None

    if scalar("id") and not ID_RE.match(scalar("id")):
        res.add("FM-FORMAT", "id must be lowercase-kebab-case")
    if scalar("repo") and not REPO_NAME_RE.match(scalar("repo")):
        res.add("FM-FORMAT", "repo is the plain repo name, e.g. agent-vault (not owner/name)")
    for key in ("issue", "pr"):
        if scalar(key) and not NUMBER_RE.match(scalar(key)):
            res.add("FM-FORMAT", f"'{key}' must be a number like 12")
    if scalar("base_sha") and not SHA_RE.match(scalar("base_sha")):
        res.add("FM-FORMAT", "base_sha must be a 7-40 character lowercase hex sha")
    repos = fm.get("repos")
    if repos is not None and repos != "":
        if not isinstance(repos, list):
            res.add("FM-FORMAT", "repos must be a list like [owner/name] (use [] for none)")
        else:
            for slug in repos:
                if not REPO_SLUG_RE.match(slug):
                    res.add("FM-FORMAT", f"repos entry {slug!r} must be owner/name")
    if ntype == "idea":
        if scalar("source") and not IDEA_SOURCE_RE.match(scalar("source")):
            res.add("FM-FORMAT", "source must be 'retro <repo>#<n>', 'phone:<path>' or 'session'")
        elif scalar("source") and scalar("source").startswith("phone:")                 and not (VAULT / scalar("source")[6:]).is_file():
            res.add("FM-FORMAT", f"source path {scalar('source')[6:]!r} doesn't exist on the phone side")
        if scalar("project") and not ID_RE.match(scalar("project")):
            res.add("FM-FORMAT", "project is a plain project id, not a link")
        promoted = fm.get("promoted_to") not in (None, "")
        if (status == "promoted") != promoted and status in STATUSES["idea"]:
            res.add("FM-COND", "promoted_to must be set if and only if status is promoted")
    if ntype == "plan" and status == "sealed":
        for key in ("issue_updated", "base_sha"):
            if fm.get(key) in (None, ""):
                res.add("PLAN-SEALED", f"a sealed plan needs '{key}'")
    for key, want in derived.items():
        if isinstance(fm.get(key), str) and fm[key] != want:
            res.add("FM-MATCH", f"'{key}' is {fm[key]!r} but the file name says {want!r}")


def _check_sections(res: Result, ntype: str, secs: list) -> None:
    names = [n for n, _ in secs]
    want = SECTIONS[ntype]
    for sec in want:
        if sec not in names:
            res.add("SEC-MISSING", f"missing section '## {sec}'")
    extra = [n for i, n in enumerate(names) if n not in want or n in names[:i]]
    for n in extra:
        res.add("SEC-EXTRA", f"unexpected or duplicate section '## {n}' (allowed: {', '.join(want) or 'none'})")
    present = [n for i, n in enumerate(names) if n in want and n not in names[:i]]
    if present != [s for s in want if s in present]:
        res.add("SEC-ORDER", f"sections must appear in this order: {', '.join(want)}")


def _check_line_formats(res: Result, ntype: str, secs: list) -> None:
    by_name = {}
    for name, body in secs:
        by_name.setdefault(name, body)
    for name, (rule, none_ok) in SECTION_LINES.get(ntype, {}).items():
        for ln in by_name.get(name, []):
            if not ln.strip() or ln[0] in " \t":
                continue
            if none_ok and ln.strip() == NONE_LINE:
                continue
            if not any(rx.match(ln) for _, rx in LINE_FORMATS[rule]):
                shapes = " | ".join(s for s, _ in LINE_FORMATS[rule])
                res.add(rule, f"'## {name}' line must look like {shapes}{' (or None)' if none_ok else ''}: {ln[:60]!r}")


def _check_plan(res: Result, secs: list, draft: bool = False) -> None:
    soft = draft   # completeness rules only warn on a draft; format rules stay errors
    by_name = {}
    for name, body in secs:
        by_name.setdefault(name, body)
    acs = [int(m.group(1)) for ln in by_name.get("Acceptance criteria", [])
           if (m := AC_LINE_RE.match(ln))]
    if not acs:
        res.add("PLAN-AC", "'## Acceptance criteria' needs at least one '- AC<n>: …' line", soft)
    for n in sorted({a for a in acs if acs.count(a) > 1}):
        res.add("PLAN-AC", f"AC{n} is defined more than once")

    tests, full = [], False
    for ln in by_name.get("Tests", []):
        if m := TEST_LINE_RE.match(ln):
            tests.append(m.group(1))
        elif ln.startswith("Full check:") and LINE_FORMATS["PLAN-T"][1][1].match(ln):
            full = True
    if not tests:
        res.add("PLAN-T", "'## Tests' needs at least one '- T<n> → AC<m>: …' line", soft)
    if not full:
        res.add("PLAN-FULL", "'## Tests' needs a 'Full check: <command>' line", soft)
    referenced = {int(n) for t in tests for n in re.findall(r"AC(\d+)", t)}
    for n in sorted(set(acs) - referenced):
        res.add("PLAN-COVER", f"AC{n} is not referenced by any test", soft)
    for n in sorted(referenced - set(acs)):
        res.add("PLAN-REF", f"a test references AC{n}, which doesn't exist")

    steps, cur = [], None
    for ln in by_name.get("Steps", []):
        if ln.startswith("### "):
            if not STEP_HEADING_RE.match(ln):
                res.add("PLAN-STEP", f"step heading must look like '### <n>. <title>': {ln[:60]!r}")
            cur = (ln, [])
            steps.append(cur)
        elif cur:
            cur[1].append(ln)
    if not steps:
        res.add("PLAN-STEP", "'## Steps' needs at least one '### <n>. <title>' step", soft)
    for head, body in steps:
        for label in ("Files:", "Done when:"):
            if not any(re.match(rf"^{label} ?\S", b) for b in body):
                res.add("PLAN-STEP", f"step {head[4:30]!r} has no '{label}' line")


def validate_file(path: Path, index: Index | None = None, text: str | None = None) -> Result:
    """Validate a note; `text` checks that content as if it were in the file (nothing is written)."""
    res = Result()
    parts = path.relative_to(AGENT).parts
    if any(p.startswith(".") for p in parts):
        return res
    etype, derived, problem = classify(parts)
    if problem:
        res.add(*problem)
        return res

    index = index or Index()
    fm, body, errors = split_frontmatter(read_text(path) if text is None else text)
    for e in errors:
        res.add("FM-PARSE", e)
    if fm is None:
        if not errors:
            res.add("FM-MISSING", "add a frontmatter block; see `schema` for the fields of this type")
        return res
    if fm.get("type") != etype:
        res.add("FM-TYPE", f"type is {fm.get('type')!r} but {'/'.join(parts)} must be {etype!r}")
        return res

    _check_frontmatter(res, etype, fm, derived)
    lines = blank_code_blocks(body)
    secs = split_sections(lines)
    _check_sections(res, etype, secs)
    _check_line_formats(res, etype, secs)
    if etype == "plan":
        _check_plan(res, secs, draft=fm.get("status") == "draft")

    n_lines = len(body.splitlines())
    if etype in SIZE_CAPS and n_lines > SIZE_CAPS[etype]:
        res.add("SIZE-CAP", f"{etype} body is {n_lines} lines; the cap is {SIZE_CAPS[etype]}")
    if etype == "plan" and n_lines > PLAN_WARN_LINES:
        res.add("SIZE-WARN", f"plan body is {n_lines} lines (over {PLAN_WARN_LINES}); consider splitting the issue")

    # cross-note checks
    if etype == "project" and isinstance(fm.get("repos"), list):
        for slug in fm["repos"]:
            owners = index.repos.get(slug.split("/")[-1], [])
            if len(owners) > 1 or (len(owners) == 1 and owners[0] != path.stem):
                res.add("X-REPO-DUP", f"repo '{slug.split('/')[-1]}' is listed by more than one project: "
                        f"{', '.join(sorted(set(owners) | {path.stem}))}")
    if etype in ("plan", "impl", "retro") and parts[1] not in index.repos:
        res.add("X-REPO-UNREG", f"Work/{parts[1]}/ has no project listing repo '{parts[1]}' in repos:")
    if etype == "idea" and fm.get("project") and ID_RE.match(fm["project"]) \
            and fm["project"] not in index.projects:
        res.add("X-PROJECT", f"project '{fm['project']}' does not exist (see Agent/Projects)")
    seen = set()
    for ln in lines:
        for target in WIKILINK_RE.findall(re.sub(r"(`+).+?\1", "", ln)):
            if target not in seen and not index.resolves(target):
                seen.add(target)
                res.add("X-LINK", f"broken link [[{target}]]")
    return res


def validate_all() -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """(errors, warnings), each mapping vault-relative path -> messages."""
    index = Index()
    errors, warnings = {}, {}
    for p in sorted(AGENT.rglob("*")):
        if not p.is_file() or is_hidden(p):
            continue
        res = validate_file(p, index)
        if res.errors:
            errors[rel(p)] = res.errors
        if res.warnings:
            warnings[rel(p)] = res.warnings
    return errors, warnings


def schema_text() -> str:
    """The full note rules, printed by `vault.py schema`, from the constants above."""
    out = ["Layout (anything else in Agent/ is a stray file; dotfiles are ignored):"]
    out += [f"  {t:8} Agent/{PATHS[t]}" for t in TYPES]
    out.append("Note types: required fields; optional fields (tags and aliases are always allowed).")
    for t in TYPES:
        opt = OPTIONAL[t] + ALWAYS_ALLOWED
        out.append(f"  {t}: required: {', '.join(REQUIRED[t])}; optional: {', '.join(opt)}")
        if t in CONDITIONAL:
            out.append(f"    {CONDITIONAL[t]}")
        if t in STATUSES:
            out.append(f"    status: {' | '.join(STATUSES[t])}")
        out.append(f"    sections (in order): {', '.join('## ' + s for s in SECTIONS[t]) or '(none)'}")
        if t in SIZE_CAPS:
            out.append(f"    body cap: {SIZE_CAPS[t]} lines (error)")
        if t == "plan":
            out.append(f"    body above {PLAN_WARN_LINES} lines warns")
    out.append("Formats: dates YYYY-MM-DD; timestamps like 2026-09-26T21:04:00Z; "
               "repos entries owner/name; repo, issue, pr are plain values; ids are lowercase-kebab-case.")
    out.append("Idea source: retro <repo>#<n> | phone:<path> | session. "
               "Wikilinks in frontmatter must be quoted.")
    out.append("Line formats (a line under these sections must match; 'None' where noted):")
    for t, secs in SECTION_LINES.items():
        for sec, (rule, none_ok) in secs.items():
            out.append(f"  {t} '## {sec}' [{rule}]{' (None allowed)' if none_ok else ''}:")
            out += [f"    {shape}" for shape, _ in LINE_FORMATS[rule]]
    out.append(f"Daily events: {', '.join(EVENTS)} (with <repo>#<n>); {', '.join(IDEA_EVENTS)} (with <title> and idea link).")
    out.append("Plan checks: every AC is referenced by a test and every referenced AC exists; "
               "each step has Files: and Done when:; Tests ends with a Full check: line; "
               "a sealed plan has issue_updated and base_sha. On a draft plan the completeness checks "
               f"({', '.join(DRAFT_SOFT)}) only warn.")
    out.append("Rules (validate exits 1 on errors only):")
    out += [f"  {rid:13} {sev:8} {desc}" for rid, (sev, desc) in RULES.items()]
    return "\n".join(out)



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
