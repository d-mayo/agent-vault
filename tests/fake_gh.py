"""A fake `gh` for tests: answers from a JSON state file and records every call.

Run as AGENT_VAULT_GH=<this file> with FAKE_GH_STATE=<state.json>. State shape:
  {"signed_out": false,
   "repos": {"owner/name": {"default_branch": "main", "head_sha": "<40 hex>", "labels": [...],
       "settings": {...}, "refuse_settings": false, "branches": ["feat/1-x"],
       "prs": [{"number": 1, "headRefName": "feat/1-x", "url": "..."}],
       "compare": {"total_commits": 0, "files": [{"filename": "a.py"}]},
       "issues": {"7": {"state": "OPEN", "title": "...", "created": "...Z", "body_edited": null,
                        "renamed": null, "labels": [...]}},
       "created_prs": [], "created_issues": [], "next_number": 20}},
   "calls": [[...args of every call...]]}
Unknown repos are created with defaults on first use. Tests read and edit the file directly.
"""
import json
import os
import sys
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
        pr = {"number": n, "headRefName": opt(args, "--head"), "url": url}
        repo["prs"].append(pr)
        repo["created_prs"].append({**pr, "title": opt(args, "--title"), "base": opt(args, "--base"),
                                    "body": Path(opt(args, "--body-file")).read_text(encoding="utf-8")})
        print(url)
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
