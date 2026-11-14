# Bugs branch as fix/

## Problem
A bug's branch looks like any other: `vault.py branch` takes its type from the implementing session's judgment, so nothing ties `fix/` to an issue that is actually a bug.

## Desired outcome
- `vault.py branch` gives a `bug`-labelled issue a `fix/` branch and refuses any other `--type` for it.
- It refuses `--type fix` and `--type hotfix` for an issue without the `bug` label, and still defaults other issues to `feat/`.
- implement-story's wording about choosing `--type` matches.

## Constraints
- Depends on: S2 (Every new issue declares its place) #57
- D4. A bug is always standalone, labelled bug, and branched fix/

## Out of scope
- Labelling past issues as bugs.
- Title prefixes on issues or PRs.

## Source
[[agent-vault-design-2-features-group-issues-as-github-sub-issues]] S3
