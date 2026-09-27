---
type: plan
repo: demo
issue: 1
issue_updated: 2026-09-26T21:04:00Z
base_sha: b76b70f
status: sealed
---
# demo-1-plan

## Goal
Do the thing for [[demo]].

## Acceptance criteria
- AC1: It works.
- AC2: It is fast.
- AC3: It is safe.

## Decisions
- D1: Use the stdlib, because there are no dependencies.

## Implementer's discretion
Naming.

## Context
- `lib.py`

## Steps
### 1. First
Files: `lib.py`
Do: Change it.
Done when: It passes.

### 2. Second
Files: `test.py`
Do: Test it.
Done when: Tests pass.

## Tests
- T1 → AC1: covers one.
- T2 → AC1, AC2, AC3: covers all.
Full check: `python -m unittest`

## Stop and ask if
- Stuck.

## Out of scope
- Nothing.
