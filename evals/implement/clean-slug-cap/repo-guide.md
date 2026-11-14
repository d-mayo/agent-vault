# agent-vault

## Conventions
- Standard library only; Python 3.9+; must run on Windows.
- All note rules live as constants in `agent_vault/lib.py`; `vault.py schema` prints them from there.

## Gotchas
- Test classes run at the same time, so a test must not share any path or state outside its own temp folder.
