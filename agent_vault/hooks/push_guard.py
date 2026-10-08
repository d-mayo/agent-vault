"""User-level PreToolUse hook for Bash and PowerShell: keep every Claude session off
`main`, and issues on the one template.

Blocks (exit 2, reason on stderr):
  - `gh issue create|new` aimed at a registered repo (`--repo`/`-R`, else `GH_REPO=`,
    else the clone it runs in): issues go through `vault.py issue create`;
  - `git push` whose destination is main: an explicit refspec, `--all`/`--mirror`,
    or no refspec while the repo it runs in is on main;
  - in a registered repo, removing what makes a design's feature: `gh issue edit` with
    `--remove-parent`, `--remove-sub-issue` or `--remove-label` naming `feature`,
    `gh label delete feature`, and `gh api` calls that remove a sub-issue (`removeSubIssue`,
    or a DELETE on a `sub_issue` path) or delete the `feature` label (a DELETE on
    `…/labels/feature`): only the user changes those, by hand on GitHub;
  - skipping git's own hooks: `--no-verify`, `git commit -n`, `-c core.hooksPath=…`.
The command is tokenized quote-aware, heredoc bodies are treated as data,
`cd`/`pushd` (and PowerShell's `Set-Location`/`Push-Location`/`sl`) change the
directory used for later segments, and `bash -c '…'` is checked recursively.
PowerShell-only syntax is tokenized as Bash would be. Anything unparseable is allowed: the pre-push git
hook is the second layer.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # keep __pycache__ out of iCloud

MAIN = {"main", "refs/heads/main"}
GIT_VALUE_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}
GH_VALUE_OPTS = {"-t", "--title", "-b", "--body", "-F", "--body-file", "-l", "--label", "-a", "--assignee",
                 "-m", "--milestone", "-p", "--project", "-T", "--template", "-e", "--editor"}
PUSH_VALUE_OPTS = {"--repo", "--receive-pack", "--exec", "-o", "--push-option"}
PREFIX_CMDS = {"env", "command", "time", "nohup", "exec", "sudo", "xargs",
               "then", "do", "else", "elif", "!", "{"}
PREFIX_VALUE_OPTS = {"-u", "-C", "-S", "--unset", "--chdir", "-I", "-L", "-n", "-P", "-d", "-E"}
SHELLS = {"bash", "sh", "zsh", "dash"}
# short commit flags whose value follows in the same cluster (so no -n after them)
COMMIT_VALUE_FLAGS = set("mFCcSut")
HEREDOC_RE = re.compile(r"<<-?[ \t]*\\?(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1([^\n]*)\n.*?\n[ \t]*\2[ \t]*(?=\n|$)",
                        re.S)
CD_CMDS = {"cd", "pushd", "set-location", "push-location", "sl"}
PS_PATH_FLAGS = {"-path", "-literalpath"}
SEPARATORS = set("&|;()\n")
WIN_SEP_RE = re.compile(r"(?<=[\w.:~})-])\\(?=[\w.~$])")


def block(msg: str) -> None:
    print(f"Blocked by push guard: {msg}", file=sys.stderr)
    sys.exit(2)


def current_branch(cwd: str | None) -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd or None,
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() if out.returncode == 0 else None
    except Exception:
        return None


def tokenize(command: str) -> list[str] | None:
    command = HEREDOC_RE.sub(lambda m: "<<HEREDOC" + m.group(3), command)
    command = command.replace("\\\n", " ")
    if os.name == "nt":
        # keep Windows path separators (C:\Users\me) from being read as escapes
        command = WIN_SEP_RE.sub(r"\\\\", command)
    lex = shlex.shlex(command, posix=True, punctuation_chars="&|;()<>\n")
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    # "#" starts a comment (quote-aware). Unlike bash it also cuts mid-word (a#b); acceptable here.
    try:
        return list(lex)
    except ValueError:
        return None


def segments(tokens: list[str]):
    """Split into simple commands, dropping redirections, their targets and
    comments. Yields "(" and ")" markers so subshell cd's can be scoped."""
    seg: list[str] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t and set(t) <= SEPARATORS:
            if seg:
                yield seg
            seg = []
            for ch in t:
                if ch in "()":
                    yield [ch]
        elif t and set(t) <= set("<>&") and ("<" in t or ">" in t):
            if seg and seg[-1].isdigit():
                seg.pop()                      # fd number, as in 2>&1
            i += 1                             # skip the redirect target
        else:
            seg.append(t)
        i += 1
    if seg:
        yield seg


def resolve_dir(cwd: str | None, target: str) -> str:
    def drive(t: str, must_exist: bool) -> str:
        m = re.match(r"^/([a-zA-Z])(/|$)", t) if os.name == "nt" else None   # Git Bash: /c/Users -> C:/Users
        if m and not (must_exist and not os.path.isdir(f"{m.group(1)}:/")):
            return f"{m.group(1).upper()}:/" + t[m.end():]
        return t

    target = drive(target, False)
    target = drive(os.path.expanduser(os.path.expandvars(target)), True)   # a variable's value must name a real drive
    return os.path.normpath(os.path.join(cwd or os.getcwd(), target))


def strip_prefixes(tokens: list[str]) -> list[str]:
    while tokens:
        t = tokens[0]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t):
            tokens = tokens[1:]
        elif os.path.basename(t) in PREFIX_CMDS:
            tokens = tokens[1:]
            while tokens and (tokens[0].startswith("-") or re.match(r"^[A-Za-z_]\w*=", tokens[0])):
                tokens = tokens[2:] if tokens[0] in PREFIX_VALUE_OPTS else tokens[1:]
        elif os.path.basename(t) == "timeout":
            tokens = tokens[1:]
            while tokens and tokens[0].startswith("-"):
                tokens = tokens[1:]
            tokens = tokens[1:]                # the duration
        else:
            return tokens
    return tokens


def check_git(tokens: list[str], cwd: str | None) -> None:
    i = 1
    while i < len(tokens) and tokens[i].startswith("-"):
        opt = tokens[i]
        if opt in GIT_VALUE_OPTS and i + 1 < len(tokens):
            val = tokens[i + 1]
            if opt == "-C":
                cwd = resolve_dir(cwd, val)
            if opt == "-c" and val.lower().startswith("core.hookspath"):
                block("overriding core.hooksPath switches off the repo's safety hooks.")
            i += 2
        else:
            i += 1
    if i >= len(tokens):
        return
    sub, args = tokens[i], tokens[i + 1:]

    if "--no-verify" in args:
        block("--no-verify skips the repo's safety hooks. Fix what the hook reports instead.")
    if sub == "commit":
        for a in args:
            if a.startswith("-") and not a.startswith("--"):
                for ch in a[1:]:
                    if ch == "n":
                        block("`git commit -n` is --no-verify. Fix what the hook reports instead.")
                    if ch in COMMIT_VALUE_FLAGS:
                        break
    if sub == "config" and any(a.lower() == "core.hookspath" for a in args):
        reading = {"--get", "--get-all", "--list", "-l"} & set(args)
        values = [a for a in args if not a.startswith("-")]
        unsetting = {"--unset", "--unset-all"} & set(args)
        if not reading and (unsetting or len(values) > 1):
            block("changing core.hooksPath switches off the repo's safety hooks.")
    if sub != "push":
        return

    if "--all" in args or "--mirror" in args or "--branches" in args:
        block("pushing all branches would include main. Push your branch by name.")
    positional, j = [], 0
    while j < len(args):
        if args[j] in PUSH_VALUE_OPTS:
            j += 2
            continue
        if not args[j].startswith("-"):
            positional.append(args[j])
        j += 1
    refspecs = positional[1:]                  # the first positional is the remote
    for spec in refspecs:
        dst = (spec.split(":", 1)[1] if ":" in spec else spec).lstrip("+")
        if dst in MAIN:
            block("direct pushes to main are not allowed. Push a branch and open a PR.")
        if dst in ("HEAD", "@") and current_branch(cwd) == "main":
            block("you're on main; direct pushes to main are not allowed.")
    if not refspecs and "--tags" not in args and current_branch(cwd) == "main":
        block("you're on main; direct pushes to main are not allowed. Create a branch first.")


def repo_slug(value: str) -> str:
    """owner/name from `owner/name`, `host/owner/name` or a URL, lower-cased."""
    parts = [p for p in re.split(r"[/:]", value.strip().removesuffix(".git").rstrip("/")) if p]
    return "/".join(parts[-2:]).lower()


def gh_issue_create(seg: list[str]) -> tuple[bool, str | None]:
    """(is `gh issue create|new`, the --repo/-R value if any); option values are dropped
    wherever they sit, so `gh issue -R o/n create` is seen as `gh issue create`."""
    words, repo, i = [], None, 1
    while i < len(seg):
        a = seg[i]
        if a in ("-R", "--repo") and i + 1 < len(seg):
            repo = seg[i + 1]
            i += 1
        elif a.startswith("--repo="):
            repo = a[len("--repo="):]
        elif a.startswith("-R") and not a.startswith("--") and len(a) > 2:
            repo = a[2:].lstrip("=")
        elif a in GH_VALUE_OPTS:
            i += 1                           # its value may look like -R… or a word
        elif not a.startswith("-"):
            words.append(a)
        i += 1
    return words[:1] == ["issue"] and words[1:2] in (["create"], ["new"]), repo


def gh_words_and_repo(seg: list[str]) -> tuple[list[str], str | None]:
    """The words of a gh command (every token that isn't an option, the `-R`/`--repo` value dropped)
    and its --repo/-R value."""
    words, repo, i = [], None, 1
    while i < len(seg):
        a = seg[i]
        if a in ("-R", "--repo") and i + 1 < len(seg):
            repo = seg[i + 1]
            i += 1
        elif a.startswith("--repo="):
            repo = a[len("--repo="):]
        elif a.startswith("-R") and not a.startswith("--") and len(a) > 2:
            repo = a[2:].lstrip("=")
        elif not a.startswith("-"):
            words.append(a)
        i += 1
    return words, repo


def feature_link_removal(seg: list[str]) -> tuple[bool, str | None]:
    """(removes a feature's label or a sub-issue link, the repo it names if any). A command it can't
    read is not a match (d14)."""
    words, repo = gh_words_and_repo(seg)
    opts = seg[1:]
    if words[:2] == ["issue", "edit"]:
        for k, a in enumerate(opts):
            if a in ("--remove-parent", "--remove-sub-issue") or a.startswith(("--remove-parent=", "--remove-sub-issue=")):
                return True, repo
            if a == "--remove-label" or a.startswith("--remove-label="):
                value = a.split("=", 1)[1] if "=" in a else (opts[k + 1] if k + 1 < len(opts) else "")
                if "feature" in [v.strip().lower() for v in value.split(",")]:
                    return True, repo
        return False, repo
    if [w.lower() for w in words[:3]] == ["label", "delete", "feature"]:
        return True, repo
    if words[:1] == ["api"]:
        if any("removesubissue" in t.lower() for t in opts):
            return True, repo
        method = ""
        for k, a in enumerate(opts):
            if a in ("-X", "--method") and k + 1 < len(opts):
                method = opts[k + 1]
            elif a.startswith("--method="):
                method = a.split("=", 1)[1]
            elif a.startswith("-X") and len(a) > 2:
                method = a[2:]
        if method.upper() != "DELETE":
            return False, repo
        for t in opts:
            path = t.strip("/").lower()
            if t.startswith("-") or "/" not in path:
                continue
            hit = re.search(r"(^|/)sub_issues?(/|$)", path) or re.search(r"(^|/)labels/feature$", path)
            if hit:
                m = re.match(r"(?:repos/)([^/]+/[^/]+)/", path)
                return True, repo or (m.group(1) if m and "{" not in m.group(1) else None)   # {owner}/{repo} is gh's own placeholder: the clone
    return False, repo


def issue_target_registered(repo: str | None, env_repo: str | None, cwd: str | None) -> bool:
    """Whether the issue would land in a registered repo. Imports lib only now, and any
    failure means "not registered": the guard never blocks on its own bugs."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        import lib
        if lib.VAULT is None:
            return False
        target = repo or env_repo
        if target:
            slug = repo_slug(target)
            return any(v.lower() == slug for v in lib.Index().slugs.values())
        return lib.session_kind(cwd or os.getcwd())["kind"] == "repo"
    except Exception:
        return False


def check_command(command: str, cwd: str | None, depth: int = 0) -> None:
    tokens = tokenize(command)
    if tokens is None or depth > 3:
        return
    saved: list[str | None] = []
    for seg in segments(tokens):
        if seg == ["("]:
            saved.append(cwd)
            continue
        if seg == [")"]:
            cwd = saved.pop() if saved else cwd
            continue
        lead = seg[:next((i for i, t in enumerate(seg) if os.path.basename(t).lower() in ("gh", "gh.exe")),
                         len(seg))]                  # what sits in front of `gh`, e.g. GH_REPO=o/n
        env_repo = next((t.split("=", 1)[1] for t in lead if t.startswith("GH_REPO=")), None)
        seg = strip_prefixes(seg)
        if not seg:
            continue
        name = os.path.basename(seg[0])
        if name.lower() in CD_CMDS and len(seg) > 1:
            args = [a for a in seg[1:] if a.lower() not in PS_PATH_FLAGS]
            if args:
                cwd = resolve_dir(cwd, args[0])
        elif name in SHELLS:
            k = next((i for i, a in enumerate(seg[1:], 1) if re.fullmatch(r"-[a-zA-Z]*c[a-zA-Z]*", a)), None)
            if k is not None and k + 1 < len(seg):
                check_command(seg[k + 1], cwd, depth + 1)
        elif name in ("git", "git.exe"):
            check_git(seg, cwd)
        elif name.lower() in ("gh", "gh.exe"):
            is_create, repo = gh_issue_create(seg)
            if is_create and issue_target_registered(repo, env_repo, cwd):
                import lib
                block("create issues with the issue template, not `gh issue create`: "
                      f"{lib.CLI} issue create <repo> --title \"...\" --body-file <file> "
                      "(the `issue` skill drafts it).")
            removes, repo = feature_link_removal(seg)
            if removes and issue_target_registered(repo, env_repo, cwd):
                block("a feature's label and sub-issue links are never removed by a session. The user "
                      "changes them by hand on GitHub.")


def main() -> None:
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
    except Exception:
        return
    if data.get("tool_name") not in ("Bash", "PowerShell"):
        return
    command = (data.get("tool_input") or {}).get("command") or ""
    if "git" in command or "gh" in command:
        check_command(command, data.get("cwd"))


if __name__ == "__main__":
    main()
