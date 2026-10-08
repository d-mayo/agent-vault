"""A fake `gh` for tests: answers from a JSON state file and records every call.

Run as AGENT_VAULT_GH=<this file> with FAKE_GH_STATE=<state.json>. State shape:
  {"signed_out": false, "hang": false,
   "repos": {"owner/name": {"default_branch": "main", "head_sha": "<40 hex>", "labels": [...],
       "settings": {...}, "refuse_settings": false, "branches": ["feat/1-x"],
       "prs": [{"number": 1, "headRefName": "feat/1-x", "url": "...", "state": "OPEN",
                "closes": [{"number": 7, "repo": "owner/name"}],
                "checks": [{"__typename": "CheckRun", "name": "ci", "status": "COMPLETED",
                            "conclusion": "SUCCESS"}]}],  (state/closes/checks optional)
       "bare": "<path of the repo's bare remote>", "refuse_merge": "<GitHub's reason to refuse a merge>",
         (both optional: `pr view` reports a PR's head and `pr merge` squashes it from that remote),
       "compare": {"total_commits": 0, "files": [{"filename": "a.py"}]},
       "issues": {"7": {"state": "OPEN", "title": "...", "created": "...Z", "body_edited": null,
                        "renamed": null, "labels": [...], "body": "..."}},   (body optional)
       "created_prs": [], "created_issues": [], "next_number": 20}},
   "calls": [[...args of every call...]]}
Unknown repos are created with defaults on first use. Tests read and edit the file directly.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

STATE = Path(os.environ["FAKE_GH_STATE"])
DEFAULT_SETTINGS = {"allow_squash_merge": True, "allow_merge_commit": True,
                    "allow_rebase_merge": True, "delete_branch_on_merge": False}


def fail(msg, code=1):
    print(msg, file=sys.stderr)
    sys.exit(code)


def opt(args, flag, default=None):
    return args[args.index(flag) + 1] if flag in args else default


def repo_state(state, slug):
    return state["repos"].setdefault(slug, {
        "default_branch": "main", "head_sha": "a" * 40, "labels": [], "settings": dict(DEFAULT_SETTINGS),
        "refuse_settings": False, "branches": [], "prs": [], "compare": {"total_commits": 0, "files": []},
        "issues": {}, "created_prs": [], "created_issues": [], "next_number": 20})


def git(*args, cwd=None):
    proc = subprocess.run(["git", "-c", "user.name=fake-gh", "-c", "user.email=fake@example.com",
                           "-c", "commit.gpgsign=false", *args], cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8")
    if proc.returncode != 0:
        fail(f"fake gh: git {' '.join(args[:3])} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def pr_head(repo, pr):
    """The PR branch's commit on the bare remote ('' when the repo has no bare remote)."""
    if not repo.get("bare"):
        return ""
    proc = subprocess.run(["git", "--git-dir", repo["bare"], "rev-parse", "--verify", "-q",
                           f"refs/heads/{pr['headRefName']}"], capture_output=True, text=True)
    return proc.stdout.strip()


def merge_pr(repo, state, args):
    """Squash-merge a PR on the bare remote the way GitHub would, then close what it closes."""
    pr = next((p for p in repo["prs"] if str(p["number"]) == args[2]), None)
    if pr is None:
        fail(f"Could not resolve to a PullRequest with the number of {args[2]}")
    if "--squash" not in args:
        fail("fake gh: only --squash merges are supported")
    for banned in ("--delete-branch", "--admin", "--auto"):
        if banned in args:
            fail(f"fake gh: {banned} is not supported")
    if repo.get("refuse_merge"):
        fail(repo["refuse_merge"])
    if pr.get("state", "OPEN") != "OPEN":
        fail(f"Pull request #{pr['number']} is not mergeable: it is {pr['state'].lower()}.")
    head = pr_head(repo, pr)
    wanted = opt(args, "--match-head-commit")
    if wanted and wanted != head:
        fail("Head branch was modified. Review and try the merge again.")
    work = tempfile.mkdtemp(prefix="fake-gh-")
    try:
        git("clone", "-q", "--branch", repo["default_branch"], repo["bare"], work)
        git("fetch", "-q", "origin", pr["headRefName"], cwd=work)
        git("merge", "--squash", "FETCH_HEAD", cwd=work)
        git("commit", "-q", "--allow-empty", "-m", f"{pr['headRefName']} (#{pr['number']})", cwd=work)
        git("push", "-q", "origin", repo["default_branch"], cwd=work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    pr["state"] = "MERGED"
    for c in pr.get("closes", []):
        issue = state["repos"].get(c["repo"], {}).get("issues", {}).get(str(c["number"]))
        if issue:
            issue["state"] = "CLOSED"


def graphql(args, state):
    fields = {a.split("=", 1)[0]: a.split("=", 1)[1] for a in args if "=" in a and not a.startswith("query=")}
    slug = f"{fields['owner']}/{fields['name']}"
    issue = repo_state(state, slug)["issues"].get(fields["number"])
    if issue is None:
        fail("GraphQL: Could not resolve to an Issue")
    nodes = [{"createdAt": issue["renamed"]}] if issue.get("renamed") else []
    data = {"state": issue["state"], "title": issue["title"], "createdAt": issue["created"],
            "url": f"https://github.com/{slug}/issues/{fields['number']}",
            "lastEditedAt": issue.get("body_edited"),
            "labels": {"nodes": [{"name": n} for n in issue["labels"]]},
            "timelineItems": {"nodes": nodes}}
    print(json.dumps({"data": {"repository": {"issue": data}}}))


def main():
    args = sys.argv[1:]
    state = json.loads(STATE.read_text(encoding="utf-8"))
    state.setdefault("calls", []).append(args)
    STATE.write_text(json.dumps(state, indent=1), encoding="utf-8")   # recorded even when we fail below
    if state.get("hang"):
        time.sleep(60)
    if state.get("signed_out"):
        fail("To get started with GitHub CLI, please run:  gh auth login", 4)
    slug = opt(args, "--repo")
    repo = repo_state(state, slug) if slug else None
    cmd = args[:2]
    if cmd == ["label", "list"]:
        print(json.dumps([{"name": n} for n in repo["labels"]]))
    elif cmd == ["label", "create"]:
        repo["labels"].append(args[2])
    elif cmd == ["issue", "edit"]:
        label = opt(args, "--add-label")
        if label not in repo["labels"]:
            fail(f"failed to update: '{label}' not found")
        issue = repo["issues"][args[2]]
        if label not in issue["labels"]:
            issue["labels"].append(label)
    elif cmd == ["issue", "list"]:
        wanted = opt(args, "--state", "open").upper()
        print(json.dumps([{"number": int(n), "title": i["title"], "state": i["state"], "body": i.get("body", ""),
                           "url": f"https://github.com/{slug}/issues/{n}"}
                          for n, i in sorted(repo["issues"].items(), key=lambda kv: int(kv[0]))
                          if wanted == "ALL" or i["state"] == wanted]))
    elif cmd == ["issue", "create"]:
        n = repo["next_number"]
        repo["next_number"] += 1
        url = f"https://github.com/{slug}/issues/{n}"
        repo["created_issues"].append({"number": n, "title": opt(args, "--title"), "url": url,
                                       "body": Path(opt(args, "--body-file")).read_text(encoding="utf-8")})
        print(url)
    elif cmd == ["repo", "view"]:
        repo = repo_state(state, args[2])
        print(json.dumps({"defaultBranchRef": {"name": repo["default_branch"]}}))
    elif cmd == ["pr", "list"]:
        print(json.dumps(repo["prs"]))
    elif cmd == ["pr", "create"]:
        n = repo["next_number"]
        repo["next_number"] += 1
        url = f"https://github.com/{slug}/pull/{n}"
        body = Path(opt(args, "--body-file")).read_text(encoding="utf-8")
        closes = [{"number": int(m), "repo": slug} for m in re.findall(r"(?im)^closes #(\d+)", body)]
        pr = {"number": n, "headRefName": opt(args, "--head"), "url": url}
        repo["prs"].append({**pr, "state": "OPEN", "closes": closes})
        repo["created_prs"].append({**pr, "title": opt(args, "--title"), "base": opt(args, "--base"),
                                    "body": body})
        print(url)
    elif cmd == ["pr", "view"]:
        pr = next((p for p in repo["prs"] if str(p["number"]) == args[2]), None)
        if pr is None:
            fail(f"Could not resolve to a PullRequest with the number of {args[2]}")
        refs = [{"number": c["number"], "repository": {"name": c["repo"].split("/")[1],
                                                       "owner": {"login": c["repo"].split("/")[0]}}}
                for c in pr.get("closes", [])]
        print(json.dumps({"state": pr.get("state", "OPEN"), "closingIssuesReferences": refs,
                          "headRefOid": pr_head(repo, pr), "statusCheckRollup": pr.get("checks", [])}))
    elif cmd == ["pr", "merge"]:
        merge_pr(repo, state, args)
    elif cmd[0] == "api":
        rest = [a for a in args[1:] if a != "--paginate"]
        if rest[0] == "graphql":
            graphql(args, state)
        else:
            patch = rest[0] == "-X" and rest[1] == "PATCH"
            path = (rest[2] if patch else rest[0]).split("/")
            repo = repo_state(state, "/".join(path[1:3]))
            if patch:
                if repo["refuse_settings"]:
                    fail("gh: Must have admin rights to Repository. (HTTP 403)")
                for i, a in enumerate(rest):
                    if a == "-F":
                        k, v = rest[i + 1].split("=")
                        repo["settings"][k] = v == "true"
            elif len(path) == 3:
                print(json.dumps({"name": path[2], **repo["settings"]}))
            elif path[3] == "commits":
                print(json.dumps({"sha": repo["head_sha"]}))
            elif path[3] == "compare":
                print(json.dumps(repo["compare"]))
            elif path[3] == "git":
                print("\n".join(f"refs/heads/{b}" for b in repo["branches"]))
            else:
                fail(f"fake gh: unhandled api path {'/'.join(path)}")
    else:
        fail(f"fake gh: unhandled command {args}")
    STATE.write_text(json.dumps(state, indent=1), encoding="utf-8")


main()
