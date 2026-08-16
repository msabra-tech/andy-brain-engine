# Andy Brain Engine

Andy Brain is a Windows-local engine that helps Claude Desktop turn Andy's approved work sources into a refined Obsidian Command Center.

Claude is the assistant: it analyzes, groups, prioritizes, researches, and proposes. The engine gives Claude controlled tools and renders approved results into Obsidian. Obsidian is the durable visual map.

## What it does

- Retrieves current material from approved local folders without retaining raw copies.
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
py -3 scripts\setup_windows.py
```

The setup flow creates a local Obsidian vault, ignored local configuration, temporary staging, and the Command Center. It does not copy source documents into the vault.

## Claude Desktop MCP

After setup, configure Claude Desktop to run:

```text
python C:\Tools\andy-brain-engine\brain mcp
```

The MCP server provides controlled tools for source sync, Command Center context, proposed updates, Chat Handoffs, presentation changes, and connector drafts. See [Claude Desktop integration](docs/CLAUDE_DESKTOP.md).

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
