# Andy Brain Engine

## Product Shape

Andy Brain is a Windows-local engine that helps Claude Desktop turn approved work sources into a refined Obsidian Command Center. Claude is the reasoning and research assistant; the local engine handles source access, temporary processing, approval boundaries, rendering, tests, and rollback.

## Architecture

- Local engine: code, prompts, tests, connector configuration, temporary processing, source metadata, sync status, change workspaces, backups, and Git history.
- Claude Desktop MCP server: controlled tools for sync, vault context, proposed updates, research handoffs, and system-change plans.
- Obsidian vault: canonical human-readable, refined operational knowledge. It contains no secrets, raw source copies, runtime state, or developer machinery.
- Approved sources: local folders, Google Drive, Notion, and future connectors. Source material is fetched only for an explicit use case and discarded after processing.
- Windows notification layer: reminders open a Claude review; they do not silently modify the vault or external sources.

## Source Of Truth

The vault is the durable knowledge map: Command Center, workstreams, people, decisions, insights, and chat handoffs. The engine may retain only minimal source metadata and operational state outside the vault. It must not archive or mirror raw source documents by default.

## Safety

- Never store secrets in the vault, repository, generated documentation, or source metadata.
- Do not retain raw source content after a sync/review flow completes.
- Claude may prepare changes, but meaningful vault migrations, connector/code changes, and external writes require an explicit approval step.
- Every presentation migration requires a backup and must be reversible.
- Credentials belong in the Windows credential store during deployment, never in local configuration committed to Git.

## Setup Rule

The Windows setup flow creates a local Obsidian vault, writes ignored local configuration, installs the private Claude Desktop MCP server, configures source scopes, and validates the Command Center. Daily work happens in Claude Desktop and Obsidian, not in a separate dashboard.

## Required Checks

- `python3 -m unittest discover -s tests -p 'test_*.py'`
- `python3 scripts/setup.py --dry-run --owner-name "Example Owner" --vault-title "Example Brain" --yes`
- `./brain verify`

Definition of done: tests pass, the setup dry-run works, the vault contains only refined knowledge, temporary source content is not persisted, and Claude-facing mutations are reviewable and reversible.
