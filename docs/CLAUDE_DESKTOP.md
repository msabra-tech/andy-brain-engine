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
4. Call `propose_workstream_update`, `propose_research_update`, or `propose_presentation_change`. Every recommended priority must include a score and plain-English reasoning.
5. Ask Andy for approval.
6. For any local, Drive, or Notion write, preview the exact artifact and call `apply_proposal` only with `confirmed: true` for that one write.
7. Save a refined Chat Handoff before ending meaningful work.

## Current tools

- `sync_sources`
- `sync_google_drive`
- `sync_notion`
- `get_command_center`
- `list_proposals`
- `propose_workstream_update`
- `propose_priority_override`
- `propose_research_update`
- `propose_external_write`
- `propose_system_change`
- `propose_presentation_change`
- `apply_proposal`
- `save_chat_handoff`
- `connector_status`
- `create_connector_draft`

The engine stores source metadata only. Every sync tool returns temporary excerpts for the active tool call and does not archive a source body. Google Drive and Notion must first be configured locally on Andy's Windows account; see [Connector setup](CONNECTORS.md).

## System changes from a Claude prompt

When Andy asks to change a connector, prompt, priority workflow, or Obsidian presentation, Claude must create a unified diff and call `propose_system_change`. The engine applies that diff only in an isolated workspace, runs compilation and the acceptance suite, and returns the files/tests to Andy. `apply_proposal` changes the live engine only after Andy explicitly confirms the tested proposal; the engine first creates a local backup and rolls back if post-install checks fail.

## Continuing after a closed chat

Claude chat history is useful, but the durable fallback is:

```text
40 Chat Handoffs/Current Context.md
```

Andy can start a new conversation with: “Continue my Andy Brain.” Claude should load the Command Center and current handoff before doing new research or source retrieval.
