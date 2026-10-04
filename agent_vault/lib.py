"""Shared rules and helpers for the Agent/ workflow system.

Everything that defines "a valid note" lives here, in one place. The CLI
(vault.py), the hooks, and the `vault.py schema` output all read from these
constants, so the rules can't drift apart. Standard library only.
"""
from __future__ import annotations

import datetime as dt
import hashlib
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
TYPES = ["project", "plan", "impl", "retro", "idea", "decision", "design", "daily"]
FOLDERS = {                                # folder key -> folder under Agent/
    "project": "Projects",
    "work": "Work",
    "idea": "Ideas",
    "decision": "Decisions",
    "design": "Designs",
    "daily": "Daily",
}
PATHS = {                                  # note type -> layout (for `schema`)
    "project": "Projects/<id>.md",
    "plan": "Work/<repo>/<repo>-<n>-plan.md",
    "impl": "Work/<repo>/<repo>-<n>-impl.md",
    "retro": "Work/<repo>/<repo>-<n>-retro.md",
    "idea": "Ideas/<YYYY-MM-DD>-<slug>.md",
    "decision": "Decisions/<project>/<project>-d<n>-<slug>.md",
    "design": "Designs/<project>/<project>-design-<n>-<slug>.md",
    "daily": "Daily/<YYYY-MM-DD>.md",
}
REQUIRED = {
    "project": ["type", "id", "status", "repos"],
    "plan": ["type", "repo", "issue", "status"],
    "impl": ["type", "repo", "issue", "status"],
    "retro": ["type", "repo", "issue", "pr", "status"],
    "idea": ["type", "status", "created", "source"],
    "decision": ["type", "project", "status", "created", "source"],
    "design": ["type", "project", "status", "created"],
    "daily": ["type", "date"],
}
OPTIONAL = {
    "project": ["audited"],
    "plan": ["issue_updated", "base_sha"],
    "impl": ["branch", "pr"],
    "retro": [],
    "idea": ["project", "promoted_to"],
    "decision": ["replaces", "superseded_by"],
    "design": ["sealed", "extends", "replaces"],
    "daily": [],
}
CONDITIONAL = {                            # shown by `schema`; enforced by FM-COND / PLAN-SEALED
    "plan": "issue_updated and base_sha are required when status is sealed",
    "idea": "promoted_to is required if and only if status is promoted",
    "decision": "superseded_by is required if and only if status is superseded",
    "design": "sealed is required if and only if status is sealed; extends and replaces each link a sealed design of the same project",
}
ALWAYS_ALLOWED = ["tags", "aliases"]
STATUSES = {
    "project": ["active", "paused", "archived"],
    "plan": ["draft", "sealed"],
    "impl": ["open", "sealed"],
    "retro": ["open", "sealed"],
    "idea": ["open", "promoted", "dropped"],
    "decision": ["active", "superseded"],
    "design": ["draft", "sealed"],
}
DATE_FIELDS = {"project": ["audited"], "idea": ["created"], "decision": ["created"],
               "design": ["created", "sealed"], "daily": ["date"]}
TIMESTAMP_FIELDS = {"plan": ["issue_updated"]}
SECTIONS = {                               # required `##` sections, in order
    "project": ["Purpose", "Current state", "Architecture", "Standing decisions"],
    "plan": ["Goal", "Acceptance criteria", "Decisions", "Implementer's discretion",
             "Context", "Steps", "Tests", "Stop and ask if", "Out of scope"],
    "impl": ["Deviations", "Review findings", "Verification", "Discoveries"],
    "retro": ["Summary", "CLAUDE.md audit", "README audit", "Overview audit", "Follow-ups"],
    "idea": [],
    "decision": ["Decision", "Why"],
    "design": ["Problem", "Goals", "Non-goals", "Ideas", "Options considered", "Chosen design",
               "Decisions", "Stories", "Open questions"],
    "daily": ["Log"],
}
SIZE_CAPS = {"project": 60, "impl": 80, "retro": 40, "idea": 8, "decision": 12}   # body lines; errors
WARN_LINES = {"plan": 400, "design": 400}                        # body lines; warning
EVENTS = ["planned", "started", "pr-opened", "retro-done"]          # '<repo>#<n> <event>' lines
IDEA_EVENTS = ["idea-added", "idea-promoted"]                       # '<event> — <title> [[<idea>]]' lines
DECISION_EVENT = "decision-recorded"                                # '<event> — <title> [[<record>]]' lines
DESIGN_EVENT = "design-sealed"                                      # '<event> — <title> [[<design>]]' lines
# Completeness rules that only warn while a plan is a draft (errors once sealed).
DRAFT_SOFT = ["PLAN-AC", "PLAN-STEP", "PLAN-T", "PLAN-COVER", "PLAN-FULL"]
DESIGN_SOFT = ["DESIGN-DEC", "DESIGN-STORY", "DESIGN-IDEA"]         # the same, for a draft design
BRANCH_TYPES = ["feat", "fix", "chore", "docs", "refactor", "test", "perf", "hotfix"]   # design §4
BRANCH_RE = re.compile(rf"^(?:{'|'.join(BRANCH_TYPES)})/[0-9]+-[a-z0-9-]+$")
PLANNED_LABEL = ("planned", "0e8a16", "A sealed plan exists (set by agent-vault)")   # name, color, description
HOOK_MARKER = "agent-vault-hook: pre-push"        # in githooks/pre-push; repo-init recognises the hook by it
LOG_HEADING = "## Log"
STANDING = "Standing decisions"            # the project overview section generated from the active records
LASTING_SUFFIX = " (lasting)"              # ends a plan decision line that is a lasting decision
NONE_LINE = "None"
ISSUE_SECTIONS = {                         # GitHub issue body: `##` sections, in order, with a hint each
    "Problem": "What is wrong or missing today, and for whom.",
    "Desired outcome": "What should be true once this is done, observable from outside.",
    "Constraints": "Rules the solution must respect; write None if there are none.",
    "Out of scope": "What this issue deliberately does not cover; write None if nothing.",
    "Source": "Where this came from: an idea note, a retro <repo>#<n>, a session; write None if nothing.",
}
ISSUE_TEMPLATE_PATH = ".github/ISSUE_TEMPLATE/issue.md"      # in a repo; repo-init writes it

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
DECISION_SOURCE_RE = re.compile(r"^(?:plan [A-Za-z0-9._-]+#[1-9]\d* D[1-9]\d*|design [a-z0-9]+(?:-[a-z0-9]+)* D[1-9]\d*"
                                r"|(?:issue|retro) [A-Za-z0-9._-]+#[1-9]\d*|session)$")
UNIQUE_SOURCE_RE = re.compile(r"^(?:plan [A-Za-z0-9._-]+#[1-9]\d*|design [a-z0-9]+(?:-[a-z0-9]+)*) D[1-9]\d*$")  # one record each
ISSUE_URL_RE = re.compile(r"^https://github\.com/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+/issues/[1-9]\d*$")
DESIGN_SLUG_RE = re.compile(r"^[1-9]\d*-[a-z0-9]+(?:-[a-z0-9]+)*$")      # after '<project>-design-'
DESIGN_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*-design-[1-9]\d*-[a-z0-9]+(?:-[a-z0-9]+)*$")
DECISION_HEAD_RE = re.compile(r"^### D([1-9]\d*)\. (\S.*)$")
STORY_HEAD_RE = re.compile(r"^### S([1-9]\d*)\. (\S.*)$")
IDEA_LINE_RE = re.compile(r"^- \[\[([^\]|#]+)\]\]$")
DECISION_SLUG_RE = re.compile(r"^[1-9]\d*-[a-z0-9]+(?:-[a-z0-9]+)*$")      # after '<project>-d'
LINK_VALUE_RE = re.compile(r"^\[\[([^\]|#]+)\]\]$")                   # a frontmatter value that is one wikilink
STORY_LABELS = {"Outcome": re.compile(r"\S"),
                "Depends on": re.compile(r"^(?:None|S[1-9]\d*(?:, S[1-9]\d*)*)$"),
                "Repo": REPO_NAME_RE}
DECISION_LABELS = {"Decision": re.compile(r"\S"), "Why": re.compile(r"\S"), "Replaces": LINK_VALUE_RE}
REPLACES_RE = re.compile(r"\breplaces \[\[([^\]|#]+)\]\]")             # in a plan decision line
PLAN_DECISION_RE = re.compile(r"^- D(\d+): (.+)$")
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
STEP_HEADING_RE = re.compile(r"^### [1-9]\d*\. \S")
AC_LINE_RE = re.compile(r"^- AC(\d+): \S")
TEST_LINE_RE = re.compile(r"^- T\d+ → (AC\d+(?:, AC\d+)*): \S")
_TIME = r"(?:[01]\d|2[0-3]):[0-5]\d"

# Optional trailing review-finding reference on a retro follow-up line: " (R2)" or " (R2, R5)".
FINDING_REFS = r"(?: \(R\d+(?:, R\d+)*\))?"
FINDING_REFS_RE = re.compile(r" \((R\d+(?:, R\d+)*)\)$")

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
                  re.compile(r"^- R\d+ \((?:major|minor)\): .+ → won't fix: \S")),
                 ("- R<n> (minor): <finding> → left open",
                  re.compile(r"^- R\d+ \(minor\): .+ → left open$"))],
    "RETRO-AUDIT": [("- <section heading>: confirmed|rewritten|removed — <reason>",
                     re.compile(r"^- .+: (?:confirmed|rewritten|removed) — \S"))],
    "RETRO-FOLLOW": [("- decision [[<record>]][ (R<n>[, R<m>…])]",
                      re.compile(rf"^- decision \[\[[^\]]+\]\]{FINDING_REFS}$")),
                     ("- issue #<n> created[ (R<n>[, R<m>…])]",
                      re.compile(rf"^- issue #[1-9]\d* created{FINDING_REFS}$")),
                     ("- issue #<n> amended[ (R<n>[, R<m>…])]",
                      re.compile(rf"^- issue #[1-9]\d* amended{FINDING_REFS}$")),
                     ("- idea [[<idea>]][ (R<n>[, R<m>…])]",
                      re.compile(rf"^- idea \[\[[^\]]+\]\]{FINDING_REFS}$")),
                     ("- dropped: <why>[ (R<n>[, R<m>…])]",
                      re.compile(r"^- dropped: (?!\(R\d+(?:, R\d+)*\)$)\S"))],
    "DAILY-LINE": [
        ("- HH:MM <repo>#<n> <event>[ — <detail>]",
         re.compile(rf"^- {_TIME} [A-Za-z0-9._-]+#[1-9]\d* (?:{'|'.join(EVENTS)})(?: — \S.*)?$")),
        ("- HH:MM session — <summary>[ [[<project>]]]",
         re.compile(rf"^- {_TIME} session — \S.*?(?: \[\[[^\]]+\]\])?$")),
        ("- HH:MM idea-added|idea-promoted — <title> [[<idea>]]",
         re.compile(rf"^- {_TIME} (?:{'|'.join(IDEA_EVENTS)}) — \S.*? \[\[[^\]]+\]\]$")),
        (f"- HH:MM {DECISION_EVENT} — <title> [[<record>]]",
         re.compile(rf"^- {_TIME} {DECISION_EVENT} — \S.*? \[\[[^\]]+\]\]$")),
        (f"- HH:MM {DESIGN_EVENT} — <title> [[<design>]]",
         re.compile(rf"^- {_TIME} {DESIGN_EVENT} — \S.*? \[\[[^\]]+\]\]$")),
    ],
}
# note type -> section -> (rule, "None" is a valid line)
SECTION_LINES = {
    "plan": {"Acceptance criteria": ("PLAN-AC", False), "Decisions": ("PLAN-D", False),
             "Tests": ("PLAN-T", False)},
    "impl": {"Deviations": ("IMPL-DEV", True), "Review findings": ("IMPL-REV", True)},
    "retro": {"CLAUDE.md audit": ("RETRO-AUDIT", False), "README audit": ("RETRO-AUDIT", True),
              "Overview audit": ("RETRO-AUDIT", False), "Follow-ups": ("RETRO-FOLLOW", True)},
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
    "FM-COND": ("error", "promoted_to is set if and only if an idea is promoted; superseded_by if and only if a decision is superseded"),
    "SEC-MISSING": ("error", "Every required '##' section must be present"),
    "SEC-ORDER": ("error", "'##' sections must appear in the specified order"),
    "SEC-EXTRA": ("error", "Other (or duplicate) '##' sections are rejected"),
    "SIZE-CAP": ("error", "Body line caps: project 60 (not counting the Standing decisions lines), impl 80, retro 40, idea 8, decision 12"),
    "SIZE-WARN": ("warning", "A plan or design over 400 body lines should be split into several issues"),
    "PLAN-AC": ("error", "Acceptance criteria are '- AC<n>: …' lines, numbers unique; at least one (warning while draft)"),
    "PLAN-D": ("error", "Decisions are '- D<n>: <decision>, because <reason>' lines"),
    "PLAN-STEP": ("error", "Each '### <n>. <title>' step has 'Files:' and 'Done when:'; at least one step (warning while draft)"),
    "PLAN-T": ("error", "Tests are '- T<n> → AC<m>[, AC<k>]: …' lines; at least one (warning while draft)"),
    "PLAN-COVER": ("error", "Every acceptance criterion is referenced by at least one test (warning while draft)"),
    "PLAN-REF": ("error", "Every AC a test references must exist"),
    "PLAN-FULL": ("error", "The Tests section has a 'Full check:' line (warning while draft)"),
    "PLAN-SEALED": ("error", "A sealed plan has issue_updated and base_sha"),
    "DESIGN-SEALED": ("error", "A sealed design has 'sealed' (a date), and a draft has none"),
    "DESIGN-DEC": ("error", "Design decisions are '### D<n>. <title>' entries (no [[ ]] in the title) with 'Decision:' and 'Why:' lines and an optional 'Replaces: [[<record>]]'; at least one (warning while draft)"),
    "DESIGN-STORY": ("error", "Design stories are '### S<n>. <title>' entries with 'Outcome:' and 'Depends on:' (None, or earlier stories like S1, S2) lines and an optional 'Repo: <name>'; at least one (warning while draft)"),
    "DESIGN-IDEA": ("error", "A design's Ideas are '- [[<idea>]]' lines, or None (warning while draft)"),
    "IMPL-DEV": ("error", "Deviation lines are '- Step <n>: …' (or None)"),
    "IMPL-REV": ("error", "Review findings end in '→ fixed in <sha>', '→ won't fix: <reason>' or, for minor only, '→ left open' (or None)"),
    "RETRO-AUDIT": ("error", "Audit lines are '- <heading>: confirmed|rewritten|removed — <reason>'"),
    "RETRO-FOLLOW": ("error", "Follow-up lines are issue created/amended, idea link, decision link or dropped, each optionally ending in ' (R<n>[, R<m>…])' for the left-open findings they settle"),
    "DAILY-LINE": ("error", "Log lines are '- HH:MM <repo>#<n> <event>[ — detail]', '- HH:MM session — …' an idea event line, a decision-recorded line or a design-sealed line"),
    "X-REPO-DUP": ("error", "A repo name appears in the repos: of at most one project"),
    "X-REPO-UNREG": ("error", "Work/<repo>/ belongs to a repo listed in some project's repos:"),
    "X-PROJECT": ("error", "An idea's or a decision record's 'project:' names an existing project (a sealed design's too)"),
    "X-DESIGN-LINK": ("error", "A design's extends and replaces each link a sealed design of the same project"),
    "X-DESIGN-SEALED": ("error", "A sealed design has a decision record with source 'design <design> D<n>' for each decision, and each listed idea is promoted to it"),
    "DEC-TITLE": ("error", "A decision record's body starts with a '# <title>' line"),
    "X-DECISION-LINK": ("error", "A record's replaces links an existing record of the same project; a superseded record's superseded_by links a record of the project that replaces it back"),
    "X-DECISION-ACTIVE": ("error", "An active record must not be replaced by another record"),
    "X-STANDING": ("error", "A project's 'Standing decisions' lists exactly its active records, in number order, as '- <title> [[<record>]]' lines (or None)"),
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


def decision_parts(project: str, stem: str):
    """(n, slug) of a decision record's file name '<project>-d<n>-<slug>', or None."""
    prefix = f"{project}-d"
    if not stem.startswith(prefix) or not DECISION_SLUG_RE.match(stem[len(prefix):]):
        return None
    n, slug = stem[len(prefix):].split("-", 1)
    return int(n), slug


def design_parts(project: str, stem: str):
    """(n, slug) of a design's file name '<project>-design-<n>-<slug>', or None."""
    prefix = f"{project}-design-"
    if not stem.startswith(prefix) or not DESIGN_SLUG_RE.match(stem[len(prefix):]):
        return None
    n, slug = stem[len(prefix):].split("-", 1)
    return int(n), slug


def decision_title(body: str) -> str | None:
    """The text of a record's '# <title>' line (the first non-blank body line), or None."""
    for ln in body.splitlines():
        if ln.strip():
            return ln[2:].strip() if ln.startswith("# ") and ln[2:].strip() else None
    return None


def read_decision(path: Path) -> dict | None:
    """A decision record as {stem, project, n, fm, title, path}, or None if its name or frontmatter isn't one."""
    parts = decision_parts(path.parent.name, path.stem)
    if not parts:
        return None
    fm, body, _ = split_frontmatter(read_text(path))
    if not fm or fm.get("type") != "decision":
        return None
    return {"stem": path.stem, "project": path.parent.name, "n": parts[0], "fm": fm,
            "title": decision_title(body) or path.stem, "path": path}


def link_target(value) -> str | None:
    """The note name in a frontmatter value that is one wikilink, e.g. '[[name]]'."""
    m = LINK_VALUE_RE.match(value) if isinstance(value, str) else None
    return m.group(1).strip() if m else None


def standing_lines(records: list[dict]) -> list[str]:
    """The lines the project overview's 'Standing decisions' must hold: its active records, in
    number order, or None."""
    active = [r for r in records if r["fm"].get("status") == "active"]
    return [f"- {r['title']} [[{r['stem']}]]" for r in sorted(active, key=lambda r: r["n"])] or [NONE_LINE]


def plan_lasting_decisions(body: str) -> list[tuple[int, str | None]]:
    """(k, record named by 'replaces [[…]]' or None) for each '## Decisions' line of a plan body that
    ends in the lasting marker."""
    secs = dict(split_sections(blank_code_blocks(body)))
    out = []
    for ln in secs.get("Decisions", []):
        m = PLAN_DECISION_RE.match(ln)
        if m and ln.endswith(LASTING_SUFFIX):
            r = REPLACES_RE.search(m.group(2))
            out.append((int(m.group(1)), r.group(1).strip() if r else None))
    return out


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
        self.decisions = {}  # project folder -> [record dicts, by number]
        for p in sorted((AGENT / FOLDERS["decision"]).glob("*/*.md")):
            rec = read_decision(p)
            if rec:
                self.decisions.setdefault(p.parent.name, []).append(rec)
        for recs in self.decisions.values():
            recs.sort(key=lambda r: r["n"])
        self.designs = {}    # design stem -> {project, n, fm, path}
        for p in sorted((AGENT / FOLDERS["design"]).glob("*/*.md")):
            parts = design_parts(p.parent.name, p.stem)
            fm, _, _ = split_frontmatter(read_text(p))
            if parts and fm and fm.get("type") == "design":
                self.designs[p.stem] = {"project": p.parent.name, "n": parts[0], "fm": fm, "path": p}
        self.ideas = {}      # idea stem -> frontmatter
        for p in sorted((AGENT / FOLDERS["idea"]).glob("*.md")):
            fm, _, _ = split_frontmatter(read_text(p))
            if fm and fm.get("type") == "idea":
                self.ideas[p.stem] = fm
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


def issue_body_problems(text: str) -> list[str]:
    """What is wrong with a GitHub issue body against ISSUE_SECTIONS; [] when it matches.
    Sections must appear once each, in order; each needs a non-blank line once HTML
    comments are removed ('None' counts); only blank lines may precede the first."""
    problems = []
    orig = text.splitlines()
    blanked = blank_code_blocks(text)
    heads = [(i, ln[3:].strip()) for i, ln in enumerate(blanked) if ln.startswith("## ")]
    first = heads[0][0] if heads else len(orig)
    if any(ln.strip() for ln in orig[:first]):
        problems.append("there is text before the first '## ' section")
    names = [n for _, n in heads]
    want = list(ISSUE_SECTIONS)
    for n in want:
        if n not in names:
            problems.append(f"missing section '## {n}'")
    for n in dict.fromkeys(names):
        if n not in want:
            problems.append(f"unknown section '## {n}'; allowed: {', '.join('## ' + w for w in want)}")
        elif names.count(n) > 1:
            problems.append(f"section '## {n}' appears {names.count(n)} times")
    known = [n for n in dict.fromkeys(names) if n in want and names.count(n) == 1]
    if known != [w for w in want if w in known]:
        problems.append("sections are out of order; use " + ", ".join("## " + w for w in want))
    for k, (i, n) in enumerate(heads):
        end = heads[k + 1][0] if k + 1 < len(heads) else len(orig)
        body = re.sub(r"<!--.*?(?:-->|\Z)", "", "\n".join(orig[i + 1:end]), flags=re.S)
        if n in want and names.count(n) == 1 and not body.strip():
            problems.append(f"section '## {n}' is empty (write {NONE_LINE} if there is nothing to say)")
    return problems


def issue_body_skeleton() -> str:
    """The bare issue body: the five headings with blank lines between."""
    return "\n\n".join(f"## {n}" for n in ISSUE_SECTIONS) + "\n"


def issue_template_file() -> str:
    """The GitHub Markdown issue template (ISSUE_TEMPLATE_PATH): a hint comment per section."""
    head = ("---\nname: Issue\nabout: One body shape for every issue, so plans can rely on it\n"
            'title: ""\nlabels: ""\n---\n')
    body = "\n".join(f"## {n}\n<!-- {h} -->\n" for n, h in ISSUE_SECTIONS.items())
    return head + body


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
    if top == FOLDERS["decision"] and depth == 3:
        if not ID_RE.match(parts[1]) or not decision_parts(parts[1], stem):
            return bad_name(PATHS["decision"])
        return "decision", {"project": parts[1]}, None
    if top == FOLDERS["design"] and depth == 3:
        if not ID_RE.match(parts[1]) or not design_parts(parts[1], stem):
            return bad_name(PATHS["design"])
        return "design", {"project": parts[1]}, None
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
        target = scalar("promoted_to")
        if target and not ISSUE_URL_RE.match(target) and not (
                link_target(target) and DESIGN_NAME_RE.match(link_target(target))):
            res.add("FM-FORMAT", "promoted_to must be a GitHub issue URL or a link to a design, "
                                 'quoted: promoted_to: "[[<design>]]"')
        if (status == "promoted") != promoted and status in STATUSES["idea"]:
            res.add("FM-COND", "promoted_to must be set if and only if status is promoted")
    if ntype == "decision":
        if scalar("source") and not DECISION_SOURCE_RE.match(scalar("source")):
            res.add("FM-FORMAT", "source must be 'plan <repo>#<n> D<k>', 'issue <repo>#<n>', "
                                 "'retro <repo>#<n>' or 'session'")
        if scalar("project") and not ID_RE.match(scalar("project")):
            res.add("FM-FORMAT", "project is a plain project id, not a link")
        for key in ("replaces", "superseded_by"):
            if fm.get(key) not in (None, "") and not link_target(fm.get(key)):
                res.add("FM-FORMAT", f"'{key}' must be one link, quoted: {key}: \"[[<record>]]\"")
        if (status == "superseded") != (fm.get("superseded_by") not in (None, ""))                 and status in STATUSES["decision"]:
            res.add("FM-COND", "superseded_by must be set if and only if status is superseded")
    if ntype == "design":
        if scalar("project") and not ID_RE.match(scalar("project")):
            res.add("FM-FORMAT", "project is a plain project id, not a link")
        for key in ("extends", "replaces"):
            if fm.get(key) not in (None, "") and not link_target(fm.get(key)):
                res.add("FM-FORMAT", f"'{key}' must be one link, quoted: {key}: \"[[<design>]]\"")
        if (status == "sealed") != (fm.get("sealed") not in (None, "")) and status in STATUSES["design"]:
            res.add("DESIGN-SEALED", "'sealed' (the date it was sealed) must be set if and only if status is sealed")
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


def _entries(lines: list[str], head_re) -> tuple[list[tuple[re.Match, list[str]]], list[str]]:
    """The '### ' entries of a section as (heading match, body lines), and problems with what isn't one."""
    entries, problems, cur = [], [], None
    for ln in lines:
        if ln.startswith("### "):
            m = head_re.match(ln)
            cur = (m, []) if m else None
            if m:
                entries.append(cur)
            else:
                problems.append(f"heading {ln[:60]!r} doesn't match the entry format")
        elif ln.strip():
            if cur is None:
                problems.append(f"line {ln[:60]!r} is outside any entry")
            else:
                cur[1].append(ln)
    return entries, problems


def _labelled(body: list[str], allowed: dict) -> tuple[dict, list[str]]:
    """The 'Label: value' lines of an entry as {label: [values]}, and problems with other lines, bad
    values and repeated labels. `allowed` maps a label to the regex its value must match."""
    found, problems = {}, []
    for ln in body:
        label, _, value = ln.partition(": ")
        if label not in allowed or not value.strip():
            problems.append(f"line {ln[:60]!r} must be one of: " + ", ".join(f"'{k}: …'" for k in allowed))
        elif not allowed[label].match(value.strip()):
            problems.append(f"'{label}:' value {value.strip()[:60]!r} is malformed")
        else:
            found.setdefault(label, []).append(value.strip())
    for label, values in found.items():
        if len(values) > 1:
            problems.append(f"'{label}:' appears {len(values)} times")
    return found, problems


def _design_section(body: str, name: str) -> list[str]:
    return dict(reversed(split_sections(blank_code_blocks(body)))).get(name, [])


def design_decisions(body: str) -> list[dict]:
    """The well-formed '### D<n>' entries of a design body: {n, title, decision, why, replaces}."""
    out = []
    for m, lines in _entries(_design_section(body, "Decisions"), DECISION_HEAD_RE)[0]:
        found, probs = _labelled(lines, DECISION_LABELS)
        if probs or "Decision" not in found or "Why" not in found:
            continue
        out.append({"n": int(m.group(1)), "title": m.group(2).strip(), "decision": found["Decision"][0],
                    "why": found["Why"][0],
                    "replaces": link_target(found["Replaces"][0]) if "Replaces" in found else None})
    return out


def design_stories(body: str) -> list[dict]:
    """The well-formed '### S<n>' entries of a design body: {n, title, outcome, depends, repo}."""
    out = []
    for m, lines in _entries(_design_section(body, "Stories"), STORY_HEAD_RE)[0]:
        found, probs = _labelled(lines, STORY_LABELS)
        if probs or "Outcome" not in found or "Depends on" not in found:
            continue
        deps = [] if found["Depends on"][0] == NONE_LINE else [int(x[1:]) for x in found["Depends on"][0].split(", ")]
        out.append({"n": int(m.group(1)), "title": m.group(2).strip(), "outcome": found["Outcome"][0],
                    "depends": deps, "repo": found["Repo"][0] if "Repo" in found else None})
    return out


def design_ideas(body: str) -> list[str]:
    """The idea names listed under a design's '## Ideas'."""
    return [m.group(1).strip() for ln in _design_section(body, "Ideas") if (m := IDEA_LINE_RE.match(ln))]


def _check_design(res: Result, body: str, draft: bool) -> None:
    soft = draft    # a draft design's format rules only warn; a sealed one's are errors

    for ln in _design_section(body, "Ideas"):
        if ln.strip() and ln.strip() != NONE_LINE and not IDEA_LINE_RE.match(ln):
            res.add("DESIGN-IDEA", f"'## Ideas' line must look like '- [[<idea>]]' (or None): {ln[:60]!r}", soft)

    entries, problems = _entries(_design_section(body, "Decisions"), DECISION_HEAD_RE)
    for p in problems:
        res.add("DESIGN-DEC", f"'## Decisions': {p}; use '### D<n>. <title>'", soft)
    if not entries:
        res.add("DESIGN-DEC", "'## Decisions' needs at least one '### D<n>. <title>' entry", soft)
    numbers = [int(m.group(1)) for m, _ in entries]
    for n in sorted({n for n in numbers if numbers.count(n) > 1}):
        res.add("DESIGN-DEC", f"D{n} is defined more than once", soft)
    for m, lines in entries:
        n = m.group(1)
        if "[[" in m.group(2) or "]]" in m.group(2):
            res.add("DESIGN-DEC", f"D{n}'s title must not contain [[ or ]]", soft)
        found, probs = _labelled(lines, DECISION_LABELS)
        for p in probs:
            res.add("DESIGN-DEC", f"D{n}: {p}", soft)
        for label in ("Decision", "Why"):
            if label not in found and not any(ln.startswith(f"{label}:") for ln in lines):
                res.add("DESIGN-DEC", f"D{n} has no '{label}:' line", soft)

    entries, problems = _entries(_design_section(body, "Stories"), STORY_HEAD_RE)
    for p in problems:
        res.add("DESIGN-STORY", f"'## Stories': {p}; use '### S<n>. <title>'", soft)
    if not entries:
        res.add("DESIGN-STORY", "'## Stories' needs at least one '### S<n>. <title>' entry", soft)
    numbers = [int(m.group(1)) for m, _ in entries]
    for n in sorted({n for n in numbers if numbers.count(n) > 1}):
        res.add("DESIGN-STORY", f"S{n} is defined more than once", soft)
    for m, lines in entries:
        n = int(m.group(1))
        found, probs = _labelled(lines, STORY_LABELS)
        for p in probs:
            res.add("DESIGN-STORY", f"S{n}: {p}", soft)
        for label in ("Outcome", "Depends on"):
            if label not in found and not any(ln.startswith(f"{label}:") for ln in lines):
                res.add("DESIGN-STORY", f"S{n} has no '{label}:' line", soft)
        if "Depends on" in found and found["Depends on"][0] != NONE_LINE:
            for dep in (int(x[1:]) for x in found["Depends on"][0].split(", ")):
                if dep >= n or dep not in numbers:
                    res.add("DESIGN-STORY", f"S{n} depends on S{dep}, which is not an earlier story", soft)


def _check_design_links(res: Result, path: Path, fm: dict, index: Index) -> None:
    for key in ("extends", "replaces"):
        target = link_target(fm.get(key))
        if target is None:
            continue
        other = index.designs.get(target)
        if other is None or other["project"] != path.parent.name or other["fm"].get("status") != "sealed" \
                or target == path.stem:
            res.add("X-DESIGN-LINK", f"{key} [[{target}]], which is not a sealed design of project '{path.parent.name}'")


def _check_design_sealed(res: Result, path: Path, body: str, index: Index) -> None:
    """A sealed design needs a record for each decision and each listed idea promoted to it (D4)."""
    by_source = {r["fm"].get("source") for r in index.decisions.get(path.parent.name, [])}
    for d in design_decisions(body):
        if f"design {path.stem} D{d['n']}" not in by_source:
            res.add("X-DESIGN-SEALED", f"D{d['n']} has no decision record with source 'design {path.stem} D{d['n']}'; "
                                       "a design is sealed with `seal design`")
    for name in design_ideas(body):
        fm = index.ideas.get(name)
        if fm is None or fm.get("status") != "promoted" or link_target(fm.get("promoted_to")) != path.stem:
            res.add("X-DESIGN-SEALED", f"idea [[{name}]] is not promoted to this design; "
                                       "a design is sealed with `seal design`")


def _check_decision(res: Result, path: Path, fm: dict, body: str, index: Index) -> None:
    """A record's checks against the other records, each reading only their existing state."""
    if decision_title(body) is None:
        res.add("DEC-TITLE", "the body must start with a '# <title>' line")
    records = {r["stem"]: r for r in index.decisions.get(path.parent.name, [])}
    replaces = link_target(fm.get("replaces"))
    if replaces and replaces not in records:
        res.add("X-DECISION-LINK", f"replaces [[{replaces}]], which is not a record of project '{path.parent.name}'")
    back = link_target(fm.get("superseded_by"))
    if back and (back not in records or link_target(records[back]["fm"].get("replaces")) != path.stem):
        res.add("X-DECISION-LINK", f"superseded_by [[{back}]], which is not a record of project "
                                   f"'{path.parent.name}' that replaces this one")
    if fm.get("status") == "active":
        for r in records.values():
            if link_target(r["fm"].get("replaces")) == path.stem:
                res.add("X-DECISION-ACTIVE", f"this record is replaced by [[{r['stem']}]] but is still active")


def _check_standing(res: Result, project: str, secs: list, index: Index) -> None:
    section = dict(secs).get(STANDING)
    if section is None:
        return
    have = [ln.strip() for ln in section if ln.strip()]
    want = standing_lines(index.decisions.get(project, []))
    if have != want:
        res.add("X-STANDING", f"'## {STANDING}' must list exactly the active decision records, as generated by "
                              f"`decision add`; expected {len(want)} line(s) (first: {want[0][:60]!r}), found {len(have)}")


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
    if etype == "design":
        _check_design(res, body, draft=fm.get("status") != "sealed")

    n_lines = len(body.splitlines())
    if etype == "project":      # the generated Standing decisions lines don't count toward the cap
        n_lines -= len(dict(split_sections(body.splitlines())).get(STANDING, []))
    if etype in SIZE_CAPS and n_lines > SIZE_CAPS[etype]:
        res.add("SIZE-CAP", f"{etype} body is {n_lines} lines; the cap is {SIZE_CAPS[etype]}")
    if etype in WARN_LINES and n_lines > WARN_LINES[etype]:
        res.add("SIZE-WARN", f"{etype} body is {n_lines} lines (over {WARN_LINES[etype]}); consider splitting the issue")

    # cross-note checks
    if etype == "project" and isinstance(fm.get("repos"), list):
        for slug in fm["repos"]:
            owners = index.repos.get(slug.split("/")[-1], [])
            if len(owners) > 1 or (len(owners) == 1 and owners[0] != path.stem):
                res.add("X-REPO-DUP", f"repo '{slug.split('/')[-1]}' is listed by more than one project: "
                        f"{', '.join(sorted(set(owners) | {path.stem}))}")
    if etype in ("plan", "impl", "retro") and parts[1] not in index.repos:
        res.add("X-REPO-UNREG", f"Work/{parts[1]}/ has no project listing repo '{parts[1]}' in repos:")
    if (etype in ("idea", "decision") or (etype == "design" and fm.get("status") == "sealed")) \
            and fm.get("project") and ID_RE.match(fm["project"]) and fm["project"] not in index.projects:
        res.add("X-PROJECT", f"project '{fm['project']}' does not exist (see Agent/Projects)")
    if etype == "decision":
        _check_decision(res, path, fm, body, index)
    if etype == "design":
        _check_design_links(res, path, fm, index)
        if fm.get("status") == "sealed":
            _check_design_sealed(res, path, body, index)
    if etype == "idea" and link_target(fm.get("promoted_to")) and link_target(fm["promoted_to"]) not in index.designs:
        res.add("FM-FORMAT", f"promoted_to [[{link_target(fm['promoted_to'])}]] is not a design in Agent/Designs")
    if etype == "project":
        _check_standing(res, path.stem, secs, index)
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
        if t in WARN_LINES:
            out.append(f"    body above {WARN_LINES[t]} lines warns" + (" (no cap)" if t == "design" else ""))
    out.append("Issue body (issue create; sections in order): "
               + ", ".join("## " + n for n in ISSUE_SECTIONS)
               + f"; once each, none empty ({NONE_LINE} counts), nothing before the first.")
    out.append("Formats: dates YYYY-MM-DD; timestamps like 2026-09-26T21:04:00Z; "
               "repos entries owner/name; repo, issue, pr are plain values; ids are lowercase-kebab-case.")
    out.append("Idea source: retro <repo>#<n> | phone:<path> | session. "
               "Wikilinks in frontmatter must be quoted.")
    out.append("Decision source: plan <repo>#<n> D<k> | design <design> D<k> | issue <repo>#<n> | retro <repo>#<n> | session. "
               "A record is never edited: `decision add` creates it, and `--replaces` marks the old one "
               "superseded. A plan decision line ending in '" + LASTING_SUFFIX + "' is a lasting decision; "
               "'replaces [[<record>]]' in it names the record it reverses.")
    out.append("Design entries (D4, D5: soft on a draft, errors once sealed): under Decisions, "
               "'### D<n>. <title>' (one line, no [[ ]]) then 'Decision:', 'Why:' and optional 'Replaces: [[<record>]]' lines; "
               "under Stories, '### S<n>. <title>' then 'Outcome:', 'Depends on:' (None, or earlier stories like S1, S2) "
               "and optional 'Repo: <name>' lines; Ideas are '- [[<idea>]]' lines or None "
               f"({', '.join(DESIGN_SOFT)}). A sealed design has `sealed`, an existing project, a record per "
               "decision with source 'design <design> D<n>' and each listed idea promoted to it; "
               "`seal design` does all of that. An idea's promoted_to is an issue URL or a link to a design.")
    out.append("Line formats (a line under these sections must match; 'None' where noted):")
    for t, secs in SECTION_LINES.items():
        for sec, (rule, none_ok) in secs.items():
            out.append(f"  {t} '## {sec}' [{rule}]{' (None allowed)' if none_ok else ''}:")
            out += [f"    {shape}" for shape, _ in LINE_FORMATS[rule]]
    out.append(f"Daily events: {', '.join(EVENTS)} (with <repo>#<n>); {', '.join(IDEA_EVENTS)} (with <title> and idea link); {DECISION_EVENT} (with <title> and record link); {DESIGN_EVENT} (with <title> and design link).")
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


# --- Hook helpers ------------------------------------------------------------------
def run_hook(main, name: str, stream=None) -> None:
    """Run a hook's main(); anything but a deliberate exit is reported and swallowed,
    so a bug here never blocks a session or a tool call."""
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print(f"(agent-vault {name} hook failed: {type(e).__name__}: {e})", file=stream or sys.stderr)


def session_kind(cwd) -> dict:
    """What kind of session this is, from its cwd (design §9): {"kind": "vault"} inside the vault,
    {"kind": "repo", repo, slug, project, root} inside a clone whose `origin` is a registered repo,
    else {"kind": None}."""
    none = {"kind": None}
    if not cwd or VAULT is None:
        return none
    if vault_parts(cwd) is not None:
        return {"kind": "vault"}
    try:
        import github
        slug = github.origin_slug(cwd)
        root = github.git("rev-parse", "--show-toplevel", cwd=cwd)
    except Exception:
        return none
    name = slug.split("/")[-1]
    index = Index()
    owners = index.repos.get(name)
    if not owners or index.slugs[name].lower() != slug.lower():
        return none
    return {"kind": "repo", "repo": name, "slug": slug, "project": owners[0], "root": root}


def is_sealed_note(path: Path) -> bool:
    """A plan, impl or retro note, or a design, whose frontmatter says `status: sealed`."""
    if not (WORK_FILE_RE.match(path.stem) or DESIGN_NAME_RE.match(path.stem)) or not path.is_file():
        return False
    fm, _, _ = split_frontmatter(read_text(path))
    return (fm or {}).get("status") == "sealed"


def _hash(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


def _record_hash(path: Path) -> str:
    """A decision record's hash without its frontmatter `status` and `superseded_by` lines: the CLI
    changes those two when a later record replaces it, and any other change is a forbidden edit."""
    lines, in_fm = [], False
    for n, ln in enumerate(read_text(path).splitlines(keepends=True)):
        if ln.strip() == "---" and (n == 0 or in_fm):
            in_fm = n == 0
        elif in_fm and re.match(r"(?:status|superseded_by):", ln):
            continue
        lines.append(ln)
    return hashlib.sha1("".join(lines).encode("utf-8")).hexdigest()


def _digest(path: Path, name: str) -> str:
    return _record_hash(path) if name.startswith(f"{AGENT.name}/{FOLDERS['decision']}/") else _hash(path)


def fingerprints() -> dict[str, str]:
    """Content hashes of every sealed note, decision record and every daily note older than today, by
    vault path. Stop compares against these to catch shell writes the guard can't see (design §9)."""
    out = {}
    for p in sorted((AGENT / FOLDERS["decision"]).glob("*/*.md")):
        out[rel(p)] = _digest(p, rel(p))
    for p in sorted([*(AGENT / FOLDERS["work"]).rglob("*.md"), *(AGENT / FOLDERS["design"]).glob("*/*.md")]):
        if is_sealed_note(p):
            out[rel(p)] = _hash(p)
    for p in sorted((AGENT / FOLDERS["daily"]).glob("*.md")):
        if p.stem < today().isoformat():
            out[rel(p)] = _hash(p)
    return out


def changed_fingerprints(before: dict) -> list[str]:
    """Vault paths from `before` whose content changed or that no longer exist."""
    changed = []
    for name, digest in sorted(before.items()):
        p = VAULT / name
        try:
            if _digest(p, name) != digest:
                changed.append(name)
        except OSError:
            changed.append(name)
    return changed


def daily_log_lines(n: int, match=None) -> list[str]:
    """The last `n` daily log lines (oldest first) as 'YYYY-MM-DD HH:MM …', optionally only those `match` accepts."""
    out: list[str] = []
    for p in sorted((AGENT / FOLDERS["daily"]).glob("*.md"), reverse=True):
        _, body, _ = split_frontmatter(read_text(p))
        lines = [f"{p.stem} {ln[2:]}" for ln in body.splitlines() if ln.startswith("- ")
                 and (match is None or match(ln))]
        out = lines[-(n - len(out)):] + out
        if len(out) >= n:
            break
    return out
