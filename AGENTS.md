# Personal Brain Engine

## Architecture

This repository enforces four layers:

- Local engine: code, prompts, tests, state, logs, hashes, raw archives, backups, adapters, scheduling, local integration config, and Git history.
- iCloud bridge: temporary transport files only.
- Obsidian vault: canonical human-readable knowledge and mobile presentation.
- Apple apps or authorized integrations: native reminder/calendar execution and device notifications.

## Source Of Truth

Canonical readable personal knowledge lives only in the vault. Raw evidence, software behavior, runtime state, connector code, and immutable archives live in the engine. Temporary payloads live in the bridge. Native notifications live in Apple Reminders, Calendar, Shortcuts, or an authorized MCP integration.

## Setup Rule

Use `./scripts/setup.sh` for new machines or new people. It writes local path/config files, creates the iCloud vault and bridge, renders `templates/vault`, and runs validation.

## Safety

Never store secrets in the vault, bridge, repo, or generated docs. Never write live Apple Reminders without `./brain reminders apply --live`. Commands must be idempotent and safe with paths containing spaces.

## Required Checks

- `python3 -m unittest discover -s tests -p 'test_*.py'`
- `./scripts/setup.sh --dry-run --owner-name "Example Owner" --vault-title "Example Brain" --yes`
- `./brain verify`

Definition of done: tests pass, setup dry-run works, vault boundary checks pass, bridge is outside the vault, engine is outside iCloud, local state is ignored, and no developer machinery remains in the vault.
