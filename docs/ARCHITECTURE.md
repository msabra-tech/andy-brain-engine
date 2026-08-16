# Andy Brain architecture

```text
Approved sources → temporary local processing → Claude Desktop reasoning → approved Obsidian Command Center
```

The local engine stores connector settings, source metadata, proposal state, backups of refined vault migrations, and testable code. It does not persist raw source material.

## Durable layers

- **Obsidian:** refined workstreams, people, decisions, insights, meeting prep, and Chat Handoffs.
- **Local engine:** the MCP server, source access boundaries, configuration, proposal state, presentation profile, tests, and rollback snapshots.
- **External sources:** authoritative original documents in Google Drive, Notion, selected folders, and future connectors.

## Default flow

1. Andy asks Claude to sync/review a question or workstream.
2. The engine fetches approved current material into the active tool response.
3. Claude proposes a refined update with evidence and a recommendation.
4. Andy accepts, modifies, or declines it.
5. The engine updates Markdown and regenerates the Command Center.
6. Temporary source content is discarded.

## Presentation evolution

The current presentation profile is local engine configuration. Claude can propose profile changes from a prompt; the engine backs up the refined vault, validates the change, then applies it after approval.
