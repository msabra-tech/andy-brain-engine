# Andy Brain Engine

Andy Brain is a Windows-local engine that helps Claude Desktop turn Andy's approved work sources into a refined Obsidian Command Center.

Claude is the assistant: it analyzes, groups, prioritizes, researches, and proposes. The engine gives Claude controlled tools and renders approved results into Obsidian. Obsidian is the durable visual map.

## What it does

- Retrieves current material from approved local folders, Google Drive, and Notion without retaining raw copies.
- Lets Claude propose workstream, research, priority, and Command Center updates.
- Requires Andy's approval before a proposal changes the vault or any external source.
- Saves refined Chat Handoffs so a new Claude conversation can continue after a shutdown.
- Renders an Obsidian Command Center for Michael's asks, active work, open threads, and meeting preparation.
- Creates a versioned presentation profile so Claude can evolve the map from a prompt.
- Generates Windows Task Scheduler and native-notification scripts for daily review reminders.

## Deliberate boundaries

- No raw source archive, document mirror, iCloud bridge, Apple integration, or custom daily dashboard application.
- No unattended Claude edits. Scheduled work may prepare a review, but Andy approves meaningful updates.
- No credentials in this repository or vault.

## Windows setup

On Windows, run [Setup Andy Brain.cmd](<Setup Andy Brain.cmd>) or:

```powershell
py -3 scripts\setup_wizard.py
```

The wizard creates a local Obsidian vault, ignored local configuration, temporary staging, a safe local output folder, and the Command Center. It can also connect Google Drive, Notion, local folders, and a daily Windows reminder. It does not copy source documents into the vault. The CLI fallback is `py -3 scripts\setup_windows.py`.

## Source connectors

Google Drive and Notion deliver current excerpts only to the active Claude review; the engine stores source metadata and hashes, not source bodies. They can also write a Claude-generated artifact only through a visible proposal and Andy's one-time confirmation for that specific write.

On Andy's Windows machine, use the short commands below after setup:

```powershell
# Import a Google Desktop OAuth client JSON from the JDS Google Cloud project.
brain connectors google-drive import-client C:\Path\to\google-desktop-client.json
brain connectors google-drive authorize

# Paste a Notion Personal Access Token at the hidden prompt.
brain connectors notion authorize
```

Then Claude can use `sync_google_drive` or `sync_notion` through MCP, or Andy can run `brain sync --connector google-drive` / `brain sync --connector notion`. Setup details and the required provider-side steps are in [Connector setup](docs/CONNECTORS.md).

## Claude Desktop MCP

After setup, configure Claude Desktop to run:

```text
python C:\Tools\andy-brain-engine\brain mcp
```

The MCP server provides controlled tools for source sync, Command Center context, priority and research updates, Chat Handoffs, presentation/system changes, and approval-gated external writes. See [Claude Desktop integration](docs/CLAUDE_DESKTOP.md) and [the product specification](docs/PRODUCT_SPEC.md).

## Core commands

```text
brain publish
brain sync
brain mcp
brain notifications summary
brain notifications install --time 09:00
brain verify
```

## Checks

```text
python3 -m unittest discover -s tests -p 'test_*.py'
python3 scripts/setup.py --dry-run --owner-name "Example Owner" --vault-title "Example Brain" --yes
./brain verify
```
