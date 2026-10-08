"""The one place that runs `gh` and `git` (design §4, §10).

Every call goes through `run`, so a missing or signed-out `gh` is reported the
same way everywhere. `AGENT_VAULT_GH` overrides where `gh` is found (tests point
it at tests/fake_gh.py). Standard library only.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
GH_ENV = "AGENT_VAULT_GH"
INSTALL_URL = "https://cli.github.com"
DEADLINE: float | None = None      # time.monotonic() value after which every call fails (hooks set it)
SIGNED_OUT_HINTS = ("gh auth login", "not logged in", "authentication required", "bad credentials")


class CmdError(Exception):
    """A gh or git call failed. `kind` is 'missing', 'signed-out' or 'failed'."""

    def __init__(self, msg: str, kind: str = "failed", stderr: str = ""):
        super().__init__(msg)
        self.kind, self.stderr = kind, stderr


def _windows_gh_paths() -> list[Path]:
    roots = [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")]
    out = [Path(r) / "GitHub CLI" / "gh.exe" for r in roots if r]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        out.append(Path(local) / "Programs" / "GitHub CLI" / "gh.exe")
    return out


def gh_command() -> list[str]:
    """The argv prefix that runs gh: AGENT_VAULT_GH, else PATH, else its standard Windows location."""
    override = os.environ.get(GH_ENV)
    if override:
        if not Path(override).is_file() and not shutil.which(override):
            raise CmdError(f"{GH_ENV} points at {override!r}, which doesn't exist", "missing")
        return [sys.executable, override] if override.lower().endswith(".py") else [override]
    found = shutil.which("gh")
    if not found and sys.platform == "win32":
        found = next((str(p) for p in _windows_gh_paths() if p.is_file()), None)
    if not found:
        raise CmdError(f"gh (the GitHub CLI) was not found on PATH or in its standard install "
                       f"location. Install it from {INSTALL_URL} and run `gh auth login`, "
                       f"or set {GH_ENV} to its path", "missing")
    return [found]


def run(argv: list[str], cwd: Path | str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run a command; raise CmdError on failure when `check`. argv[0] is 'gh' or 'git'."""
    tool = argv[0]
    if tool == "gh":
        argv = gh_command() + argv[1:]
    elif tool != "git":
        raise ValueError(f"github.run only runs gh and git, not {tool!r}")
    timeout = None
    if DEADLINE is not None:
        timeout = DEADLINE - time.monotonic()
        if timeout <= 0:
            raise CmdError("GitHub did not answer in time", "timeout")
    env = dict(os.environ, GH_PROMPT_DISABLED="1", GIT_TERMINAL_PROMPT="0", GH_NO_UPDATE_NOTIFIER="1")
    try:
        proc = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        raise CmdError("GitHub did not answer in time", "timeout")
    except FileNotFoundError:
        if tool == "git":
            raise CmdError("git was not found on PATH", "missing")
        raise CmdError(f"could not run gh ({argv[0]})", "missing")
    if check and proc.returncode != 0:
        err = (proc.stderr or proc.stdout).strip()
        low = err.lower()
        if tool == "gh" and (proc.returncode == 4 or any(h in low for h in SIGNED_OUT_HINTS)):
            raise CmdError("gh is not signed in to GitHub; run `gh auth login` and retry",
                           "signed-out", err)
        shown = " ".join((argv[len(gh_command()):] if tool == "gh" else argv[1:])[:4])
        raise CmdError(f"`{tool} {shown}` failed: {err}", "failed", err)
    return proc


def gh(*args: str, cwd=None) -> str:
    return run(["gh", *args], cwd).stdout


def gh_json(*args: str, cwd=None):
    out = gh(*args, cwd=cwd)
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        raise CmdError(f"gh {' '.join(args[:3])} returned something that isn't JSON: {out[:80]!r}")


def git(*args: str, cwd=None, check: bool = True) -> str:
    return run(["git", *args], cwd, check).stdout.strip()


def status_lines(cwd) -> list[str]:
    """`git status --porcelain`, one entry per changed path. Not stripped: the first column can be a space."""
    return run(["git", "status", "--porcelain"], cwd, check=False).stdout.splitlines()


# --- repos ------------------------------------------------------------------------
_SLUG_URL = re.compile(r"github\.com[:/]+([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+?)(?:\.git)?/?$")


def origin_slug(cwd) -> str:
    """owner/name of the clone's `origin`, read raw (insteadOf rewrites are not applied)."""
    url = git("config", "--get", "remote.origin.url", cwd=cwd, check=False)
    m = _SLUG_URL.search(url)
    if not m:
        raise CmdError(f"{cwd} has no GitHub `origin` remote (origin is {url or 'not set'})")
    return f"{m.group(1)}/{m.group(2)}"


def default_branch(slug: str) -> str:
    data = gh_json("repo", "view", slug, "--json", "defaultBranchRef")
    name = (data.get("defaultBranchRef") or {}).get("name")
    if not name:
        raise CmdError(f"{slug} has no default branch yet")
    return name


ISSUE_QUERY = """query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){
issue(number:$number){state title url createdAt lastEditedAt labels(first:100){nodes{name}}
timelineItems(itemTypes:[RENAMED_TITLE_EVENT],last:1){nodes{... on RenamedTitleEvent{createdAt}}}}}}"""


def issue_info(slug: str, number: str) -> dict:
    """state, title, url, labels and `edited`: the last title or body edit (D1), never
    `updatedAt`, which comments, labels and cross-references also bump."""
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
            "edited": max(s for s in stamps if s)}


ISSUE_LIMIT = 5000


def repo_issues(slug: str) -> list[dict]:
    """Every issue of a repo, open and closed, as {number, title, state, url, body}. Read straight from
    GitHub, never from its search index, which lags behind a just-filed issue (#37 D8)."""
    found = gh_json("issue", "list", "--repo", slug, "--state", "all", "--limit", str(ISSUE_LIMIT),
                    "--json", "number,title,state,url,body")
    if len(found) >= ISSUE_LIMIT:
        raise CmdError(f"{slug} has {ISSUE_LIMIT} or more issues; more than `design stories` can list")
    return found


def head_sha(slug: str, branch: str) -> str:
    return gh_json("api", f"repos/{slug}/commits/{branch}")["sha"]


def compare(slug: str, base: str, head: str) -> dict:
    return gh_json("api", f"repos/{slug}/compare/{base}...{head}")


def remote_branches(slug: str) -> list[str]:
    out = gh("api", "--paginate", f"repos/{slug}/git/matching-refs/heads", "--jq", ".[].ref")
    return [ln.strip()[len("refs/heads/"):] for ln in out.splitlines()
            if ln.strip().startswith("refs/heads/")]


def open_prs(slug: str) -> list[dict]:
    return gh_json("pr", "list", "--repo", slug, "--state", "open", "--limit", "200",
                   "--json", "number,headRefName,url")


PASSED_CONCLUSIONS = ("SUCCESS", "NEUTRAL", "SKIPPED")


def unpassed_checks(rollup: list) -> list[str]:
    """The checks in a `statusCheckRollup` that haven't passed, as 'name (state)': a check run passed when
    it concluded SUCCESS, NEUTRAL or SKIPPED, a status context when it is SUCCESS (#51 D3)."""
    out = []
    for check in rollup or []:
        if check.get("__typename") == "StatusContext":
            if check.get("state") != "SUCCESS":
                out.append(f"{check.get('context', '?')} ({(check.get('state') or 'unknown').lower()})")
        elif check.get("conclusion") not in PASSED_CONCLUSIONS:
            out.append(f"{check.get('name', '?')} "
                       f"({(check.get('conclusion') or check.get('status') or 'unknown').lower()})")
    return out


def pr_closing(slug: str, number: str) -> dict:
    """state, `closes` (the (issue number, owner/name) pairs GitHub says merging the PR closes, D1), `head`
    (its head commit) and `unpassed` (the checks that haven't passed, see `unpassed_checks`)."""
    data = gh_json("pr", "view", str(number), "--repo", slug, "--json",
                   "state,headRefOid,statusCheckRollup,closingIssuesReferences")
    closes = []
    for ref in data.get("closingIssuesReferences") or []:
        repo = ref.get("repository") or {}
        owner = (repo.get("owner") or {}).get("login", "")
        closes.append((str(ref["number"]), f"{owner}/{repo.get('name', '')}"))
    return {"state": data["state"], "closes": closes, "head": data.get("headRefOid") or "",
            "unpassed": unpassed_checks(data.get("statusCheckRollup"))}


def squash_merge(slug: str, number: str, sha: str) -> None:
    """Squash-merge the PR, only if its head is still `sha`; raises CmdError with GitHub's reason (#51 D1).
    No --delete-branch, --admin or --auto: GitHub's own settings decide those."""
    gh("pr", "merge", str(number), "--repo", slug, "--squash", "--match-head-commit", sha)


def label_names(slug: str) -> list[str]:
    return [x["name"] for x in gh_json("label", "list", "--repo", slug, "--limit", "500",
                                       "--json", "name")]


def ensure_label(slug: str, name: str, color: str, description: str) -> bool:
    """Create the label if missing. True if it was created."""
    if name in label_names(slug):
        return False
    gh("label", "create", name, "--repo", slug, "--color", color, "--description", description)
    return True
