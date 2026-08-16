# Claude Desktop integration

Claude Desktop is Andy Brain's main interaction surface. It connects to the local engine through a private stdio MCP server:

```text
python C:\Tools\andy-brain-engine\brain mcp
```

## Assistant contract

Claude may retrieve, compare, research, draft, and recommend. It does not silently write the vault, an external source, a connector, or engine code.

For a normal review, Claude should:

1. Call only the relevant source tool—`sync_sources`, `sync_google_drive`, or `sync_notion`—for the current question or workstream.
2. Compare temporary source excerpts with the refined Command Center and relevant workstreams.
3. Explain the evidence and recommendation.
4. Call `propose_workstream_update` or `propose_presentation_change`.
5. Ask Andy for approval.
6. Call `apply_proposal` only with `confirmed: true`.
7. Save a refined Chat Handoff before ending meaningful work.

## Current tools

- `sync_sources`
- `sync_google_drive`
- `sync_notion`
- `get_command_center`
- `list_proposals`
- `propose_workstream_update`
- `propose_presentation_change`
- `apply_proposal`
- `save_chat_handoff`
- `connector_status`
- `create_connector_draft`

The engine stores source metadata only. Every sync tool returns temporary excerpts for the active tool call and does not archive a source body. Google Drive and Notion must first be configured locally on Andy's Windows account; see [Connector setup](CONNECTORS.md).

## Continuing after a closed chat

Claude chat history is useful, but the durable fallback is:

```text
40 Chat Handoffs/Current Context.md
```

Andy can start a new conversation with: “Continue my Andy Brain.” Claude should load the Command Center and current handoff before doing new research or source retrieval.
