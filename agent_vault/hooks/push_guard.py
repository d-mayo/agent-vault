"""User-level PreToolUse hook for Bash: keep every Claude session off `main`.

Blocks (exit 2, reason on stderr):
  - `git push` whose destination is main: an explicit refspec, `--all`/`--mirror`,
    or no refspec while the repo it runs in is on main;
  - skipping git's own hooks: `--no-verify`, `git commit -n`, `-c core.hooksPath=…`.
The command is tokenized quote-aware, heredoc bodies are treated as data,
`cd`/`pushd` change the directory used for later segments, and `bash -c '…'`
is checked recursively. Anything unparseable is allowed: the pre-push git
hook is the second layer.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys

MAIN = {"main", "refs/heads/main"}
GIT_VALUE_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}
PUSH_VALUE_OPTS = {"--repo", "--receive-pack", "--exec", "-o", "--push-option"}
PREFIX_CMDS = {"env", "command", "time", "nohup", "exec", "sudo", "xargs",
               "then", "do", "else", "elif", "!", "{"}
PREFIX_VALUE_OPTS = {"-u", "-C", "-S", "--unset", "--chdir", "-I", "-L", "-n", "-P", "-d", "-E"}
SHELLS = {"bash", "sh", "zsh", "dash"}
# short commit flags whose value follows in the same cluster (so no -n after them)
COMMIT_VALUE_FLAGS = set("mFCcSut")
HEREDOC_RE = re.compile(r"<<-?[ \t]*\\?(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1([^\n]*)\n.*?\n[ \t]*\2[ \t]*(?=\n|$)",
                        re.S)
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
        seg = strip_prefixes(seg)
        if not seg:
            continue
        name = os.path.basename(seg[0])
        if name in ("cd", "pushd") and len(seg) > 1:
            cwd = resolve_dir(cwd, seg[1])
        elif name in SHELLS:
            k = next((i for i, a in enumerate(seg[1:], 1) if re.fullmatch(r"-[a-zA-Z]*c[a-zA-Z]*", a)), None)
            if k is not None and k + 1 < len(seg):
                check_command(seg[k + 1], cwd, depth + 1)
        elif name in ("git", "git.exe"):
            check_git(seg, cwd)


def main() -> None:
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
    except Exception:
        return
    if data.get("tool_name") != "Bash":
        return
    command = (data.get("tool_input") or {}).get("command") or ""
    if "git" in command:
        check_command(command, data.get("cwd"))


if __name__ == "__main__":
    main()
