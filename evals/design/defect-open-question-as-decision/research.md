base_sha: 44a3187

# Research for design 2

The design absorbs no idea (`## Ideas` says None). Below are the issues and the open idea it names, the code it changes and the tool facts it relies on, as they stood at `44a3187` (the commit before the design's first story landed; the design was sealed on 2026-10-08). Open issues are shown as filed. The design cites GitHub's documentation ("Adding sub-issues", docs.github.com) for sub-issues in other repositories; that page was not re-read for this case, and the design lists the cross-repo case as an open question.

## The open idea the design names (Non-goals)

`````markdown
# design skill read open issues of every repo in the project
From #37's review (R3): the design skill's Research step runs a bare gh issue list in the current clone only, so a project with several repos, or a design started outside a clone, never reads the other repos' open issues.
`````

## Issues the design names

- #54 "seal retro's merge messages: name the branch and quote the path", open, a retro follow-up from the retro of #51 (its `## Source` is `retro agent-vault#51`): it continues #51's work (`seal retro` merging the PR) but is filed flat.
- #51 is the issue that made `seal retro` squash-merge the PR (decision d66).
- Design 1's stories were filed flat as #43 "Run the test suite on Windows and macOS in CI", #44 "One CLI command that is the same on every machine", #45 "Claude hooks fail closed when agent-vault's Python is gone", #46 "install.py never downgrades the shared vault guide", #47 "install.py checks prerequisites and records gh and git" and #48 "Setup-neutral wording and a verified Mac install"; each carries `[[agent-vault-design-1-portable-across-machines-and-setups]] S<n>` in its Source section (decision d54).

### #54 as filed
`````markdown
## Problem
Two small defects in messages added by #51. The head-mismatch refusal in `seal retro` says "the local <folder> branch", but the name it prints is the clone's folder, not the branch. The commands printed after a failed default-branch pull use `git -C <path>` without quoting the path, so a path with spaces (common under `C:\Users\<First Last>\`) fails when pasted.

## Desired outcome
The refusal names the current branch, and the printed commands quote the path so they can be pasted as they are.

## Constraints
The refusal still names both commits, and the pull failure still names the worktree path and git's reason.

## Out of scope
Any other change to the merge flow.

## Source
retro agent-vault#51
`````

## Tool facts (gh 2.101.0, the version installed here; read from `gh issue create`, `edit`, `view` and `list` help)

`````text
gh issue create: --parent number  Set the parent issue by number or URL (and --label)
gh issue edit:   --add-sub-issue number  Add sub-issues by number or URL
                 --parent number  Set the parent issue by number or URL
                 --remove-parent, --remove-sub-issue number
gh issue view and list --json fields include: parent, subIssues, subIssuesSummary
`````

## Code and docs at 44a3187

### `agent_vault/vault.py`: `cmd_seal_plan` (lines 323-341), which adds the `planned` label
`````python
def cmd_seal_plan(args) -> None:
    slug = issue_context(args)
    path, fm, body = load_plan(args.repo, args.issue)
    name = lib.PLANNED_LABEL[0]
    if fm.get("status") == "sealed":
        if name in github.issue_info(slug, args.issue)["labels"]:
            die(f"{lib.rel(path)} is already sealed")
        add_planned_label(slug, args.issue)     # `stage` reports this state as backlog and points here
        print(f"the plan was already sealed; added the '{name}' label to {slug}#{args.issue}")
        return
    trial = {**fm, "status": "sealed", "issue_updated": "2000-01-01T00:00:00Z", "base_sha": "0000000"}
    errors = lib.validate_file(path, text=lib.render_frontmatter(trial) + body.lstrip("\n")).errors
    if errors:
        die(f"{lib.rel(path)} can't be sealed yet:\n  " + "\n  ".join(errors))
    add_planned_label(slug, args.issue)
    info = github.issue_info(slug, args.issue)          # after the label: D1 ignores label changes anyway
    sha = github.head_sha(slug, github.default_branch(slug))[:7]
    update_note(path, status="sealed", issue_updated=info["edited"], base_sha=sha)
    log_event(args.repo, args.issue, "planned")
`````

### `agent_vault/vault.py`: `cmd_branch` (lines 739-760): the branch type comes from `args.type` (`--type`, default `feat`), never from the issue
`````python
def cmd_branch(args) -> None:
    slug = issue_context(args)
    info, warnings = check_preflight(args.repo, args.issue, slug)
    for w in warnings:
        print(f"warning: {w}")
    impl, _ = load_impl(args.repo, args.issue)
    top = require_clone(slug)
    name = f"{args.type}/{args.issue}-{args.slug or default_slug(info['title'])}"
    if not lib.BRANCH_RE.match(name):
        die(f"branch '{name}' must match <type>/<issue>-<slug> with a lowercase a-z0-9- slug")
    pat = issue_branch_re(args.issue)
    local = github.git("for-each-ref", "--format=%(refname:short)", "refs/heads", cwd=top).splitlines()
    found = [f"{b} (local)" for b in local if pat.match(b)]
    found += [f"{b} (on GitHub)" for b in github.remote_branches(slug) if pat.match(b)]
    if found:
        die(f"issue #{args.issue} already has a branch: {', '.join(found)}")
    base = github.default_branch(slug)
    github.git("fetch", "origin", base, cwd=top)
    github.git("switch", "--no-track", "-c", name, f"origin/{base}", cwd=top)
    github.git("push", "-u", "origin", name, cwd=top)
    update_note(impl, branch=name)
    log_event(args.repo, args.issue, "started", name)
`````

### `agent_vault/vault.py`: `file_issue` and `cmd_issue_create` (lines 868-898): no placement flag exists today
`````python
def file_issue(index: lib.Index, repo: str, title: str, body_file: str, idea: Path | None = None) -> None:
    """Create an issue from a body file that matches the template; with an idea, promote it.
    Everything is checked before `gh` runs. The body is posted exactly as the file has it."""
    require_repo(index, repo)
    title = one_line(title)
    if not title:
        die("the title is empty")
    body = read_issue_body(body_file)
    problems = lib.issue_body_problems(body)
    if problems:
        print(f"error: {body_file} doesn't match the issue template:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        sys.exit(1)
    url = create_with_body(["issue", "create", "--repo", index.slugs[repo], "--title", title], body)
    if idea is not None:
        try:
            update_note(idea, status="promoted", promoted_to=url)
        except SystemExit:
            print(f"the issue was created: {url}", file=sys.stderr)
            raise
        log_idea_event("idea-promoted", title, idea)
        print(f"promoted {lib.rel(idea)} to {url}")
    print(url)


def cmd_issue_create(args) -> None:
    index = lib.Index()
    require_repo(index, args.repo)
    idea = idea_for_issue(index, args.idea, args.repo) if args.idea else None
    file_issue(index, args.repo, args.title, args.body_file, idea)
`````

### `agent_vault/vault.py`: `cmd_design_stories` (lines 1331-1355)
`````python
def cmd_design_stories(args) -> None:
    path = find_design(args.design)
    fm, body, _ = lib.split_frontmatter(lib.read_text(path))
    if (fm or {}).get("status") != "sealed":
        die(f"{lib.rel(path)} is a draft; stories are filed from a sealed design (`seal design {path.stem}`)")
    index = lib.Index()
    project = fm["project"]
    repos = sorted(n for n, pids in index.repos.items() if project in pids)
    filed: dict[int, list[tuple[str, dict]]] = {}
    for name in repos:
        for n, issues in story_issues(index.slugs[name], path.stem).items():
            filed.setdefault(n, []).extend(issues)
    stories = lib.design_stories(body)
    for s in stories:
        issues = filed.get(s["n"])
        print(f"S{s['n']}. {s['title']}: "
              + (", ".join(f"{slug}#{i['number']} ({i['state'].lower()})" for slug, i in issues)
                 if issues else "not filed"))
    if not repos:
        print(f"project '{project}' has no repos yet, so nothing can be filed; register one first")
    unfiled = [s for s in stories if s["n"] not in filed]
    print(f"{len(stories) - len(unfiled)} of {len(stories)} stories filed" if stories else "no stories")


def cmd_decisions(args) -> None:
`````

### `agent_vault/vault.py`: the argument parsers of `branch` and `issue create`
`````python
        p.add_argument("repo")
        p.add_argument("issue")
        if name == "branch":
            p.add_argument("--type", default="feat", choices=lib.BRANCH_TYPES)
            p.add_argument("--slug")
        if name == "open-pr":
            p.add_argument("--body-file")

        dest="action", required=True, metavar="create")
    p = issue.add_parser("create", help="refuses a body that doesn't match the issue template")
    p.add_argument("repo")
    p.add_argument("--title", required=True)
    p.add_argument("--body-file", required=True)
    p.add_argument("--idea", help="an open idea to mark promoted")
    p.set_defaults(fn=cmd_issue_create)
`````

### `agent_vault/github.py` (lines 139-166)
`````python
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
`````

### `agent_vault/lib.py` (lines 124-126)
`````python
BRANCH_TYPES = ["feat", "fix", "chore", "docs", "refactor", "test", "perf", "hotfix"]   # design §4
BRANCH_RE = re.compile(rf"^(?:{'|'.join(BRANCH_TYPES)})/[0-9]+-[a-z0-9-]+$")
PLANNED_LABEL = ("planned", "0e8a16", "A sealed plan exists (set by agent-vault)")   # name, color, description
`````


### `skills/issue/SKILL.md` (Never list and Draft step 2): no step looks at features or open work
`````markdown

- Never run `gh issue create`; the CLI is the only way an issue gets
  created (the guard would block it anyway).
- Never edit an existing issue; that is a different request and keeps its
  own approval step in the skill that asks for it.
- Never create the issue before the user has approved the exact title and
  body you showed.
- Never change the body after it was approved: what is posted is what the
  user read.

## Resolve the vault, CLI and repo

- Vault and CLI: `~/.claude/agent-vault.json`'s `vault` field is the vault
  path; the exact `vault.py` invocation is the line under "CLI after
  install" in `<vault>/CLAUDE.md`.
- Repo: for an idea, the repo of its `project:` (the project note's
  `repos:`; ask if the project has several, or the idea has none). Otherwise
  the current clone's `origin` (`git config --get remote.origin.url`, with
  the host and any trailing `.git` stripped, the part after the `/`) when
  it is registered; otherwise ask which repo.

## Draft

1. Read the body template from `vault.py schema` (the "Issue body" line):
   the `##` sections, in order, none empty. Write `None` under a section
   with nothing to say.
2. Read what the issue is about: the idea note (`Agent/Ideas/<file>.md`) or
   the user's request. Fill each section from it: `Problem` (what is wrong
   or missing, and for whom), `Desired outcome` (what is true afterwards,
   observable from outside), `Constraints`, `Out of scope`, and `Source`
   (the idea as `[[<idea>]]`, `retro <repo>#<n>`, or `session`).
3. Pick a short title on one line.
4. Where something is unclear and the request or note doesn't settle it,
   ask the user open-ended questions in prose, never multiple choice, and
   fold the answers in. Don't invent requirements.

## Approval
`````

### `templates/vault-CLAUDE.md` (the `issue create` and `repo-init` lines)
`````markdown
- To file an issue, say "file an issue for ..." or "turn this idea into an issue" (the `issue` skill); it drafts the title and body in the issue template, asks about anything unclear, and creates it through `{{CLI}} issue create` only after I approve the exact wording. Never `gh issue create`: a hook blocks it in registered repos.
- `{{CLI}} repo-init <path>`: one-time setup of a repo clone (squash-only merges, `planned` label, shared pre-push hook, `CLAUDE.md` skeleton, `.github/ISSUE_TEMPLATE/issue.md`; the last two are left uncommitted).
- `{{CLI}} issue create <repo> --title "..." --body-file <file> [--idea <file>]`: the one way to create a GitHub issue; refuses a body that isn't the issue template (`{{CLI}} schema` lists its sections), and only after I approve the exact title and body
`````

