from __future__ import annotations

import json
import sys
from typing import Any, Callable

from .command_center import publish_command_center
from .config import Config, load_config, load_runtime
from .connectors import connector_status, sync_google_drive, sync_notion
from .ephemeral import draft_connector, sync_local_sources
from .operations import (
    apply_proposal,
    list_proposals,
    propose_external_write,
    propose_presentation_change,
    propose_priority_override,
    propose_research_update,
    propose_system_change,
    propose_workstream_update,
    save_chat_handoff,
    vault_path,
)


SERVER_INFO = {"name": "andy-brain", "version": "0.1.0"}


def tool_definitions() -> list[dict[str, Any]]:
    return [
        {"name": "sync_sources", "description": "Read current approved local-source excerpts for this review. Source bodies are not persisted.", "inputSchema": {"type": "object", "properties": {"paths": {"type": "array", "items": {"type": "string"}}, "max_items": {"type": "integer"}}}},
        {"name": "sync_google_drive", "description": "Read current Google Drive content Andy has authorized into this review only. It is read-only and source bodies are not persisted.", "inputSchema": {"type": "object", "properties": {"file_ids": {"type": "array", "items": {"type": "string"}}, "query": {"type": "string"}, "max_items": {"type": "integer"}}}},
        {"name": "sync_notion", "description": "Read current Notion pages visible to Andy's configured Personal Access Token into this review only. It is read-only and source bodies are not persisted.", "inputSchema": {"type": "object", "properties": {"page_ids": {"type": "array", "items": {"type": "string"}}, "query": {"type": "string"}, "max_items": {"type": "integer"}}}},
        {"name": "get_command_center", "description": "Return the current refined Obsidian Command Center.", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "list_proposals", "description": "List proposed vault or presentation changes awaiting Andy approval.", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "propose_workstream_update", "description": "Prepare a Claude-recommended workstream update, including a visible 0-100 priority score and plain-English reasoning. It is not written until Andy confirms application.", "inputSchema": {"type": "object", "required": ["title", "summary"], "properties": {"title": {"type": "string"}, "summary": {"type": "string"}, "priority": {"type": "string", "enum": ["Critical", "High", "Normal", "Low"]}, "priority_score": {"type": "integer", "minimum": 0, "maximum": 100}, "priority_reasoning": {"type": "string"}, "recommendation": {"type": "string"}, "people": {"type": "array", "items": {"type": "string"}}, "source_links": {"type": "array", "items": {"type": "string"}}, "open_threads": {"type": "array", "items": {"type": "string"}}, "follow_ups": {"type": "array", "items": {"type": "object", "properties": {"text": {"type": "string"}, "due_date": {"type": "string"}, "reason": {"type": "string"}}}}, "research": {"type": "string"}}}},
        {"name": "propose_priority_override", "description": "Prepare Andy's manual override of Claude's suggested priority score and reasoning. It requires explicit confirmation before the vault changes.", "inputSchema": {"type": "object", "required": ["title", "priority"], "properties": {"title": {"type": "string"}, "priority": {"type": "string", "enum": ["Critical", "High", "Normal", "Low"]}, "priority_score": {"type": "integer", "minimum": 0, "maximum": 100}, "reasoning": {"type": "string"}}}},
        {"name": "propose_research_update", "description": "Prepare Claude's research findings, evidence links, recommendation, and next steps for a workstream. It requires Andy's approval before saving to Obsidian.", "inputSchema": {"type": "object", "required": ["title", "question", "findings"], "properties": {"title": {"type": "string"}, "question": {"type": "string"}, "findings": {"type": "string"}, "evidence_links": {"type": "array", "items": {"type": "string"}}, "recommendation": {"type": "string"}, "next_steps": {"type": "array", "items": {"type": "string"}}}}},
        {"name": "propose_external_write", "description": "Prepare a generated artifact for Google Drive, Notion, or Andy's local output folder. Show the preview to Andy and call apply_proposal only after he explicitly approves this individual write.", "inputSchema": {"type": "object", "required": ["connector", "title", "content"], "properties": {"connector": {"type": "string", "enum": ["google_drive", "notion", "local_file"]}, "title": {"type": "string"}, "content": {"type": "string"}, "target": {"type": "string"}}}},
        {"name": "propose_system_change", "description": "Stage a requested engine/connector/template change as a unified Git diff in an isolated workspace. The diff is compiled and tested there, then requires Andy's explicit confirmation before it can alter the live engine.", "inputSchema": {"type": "object", "required": ["title", "summary", "patch"], "properties": {"title": {"type": "string"}, "summary": {"type": "string"}, "patch": {"type": "string"}}}},
        {"name": "propose_presentation_change", "description": "Prepare a prompt-driven Obsidian presentation change. It requires a backup and explicit approval before application.", "inputSchema": {"type": "object", "required": ["summary", "profile_updates"], "properties": {"summary": {"type": "string"}, "profile_updates": {"type": "object"}, "migration": {"type": "object", "properties": {"archive_workstream_sections": {"type": "array", "items": {"type": "string"}}}}}}},
        {"name": "apply_proposal", "description": "Apply an already-proposed vault or presentation change only after Andy explicitly approves it.", "inputSchema": {"type": "object", "required": ["proposal_id", "confirmed"], "properties": {"proposal_id": {"type": "string"}, "confirmed": {"type": "boolean"}}}},
        {"name": "save_chat_handoff", "description": "Save refined session continuity to Obsidian after Andy approves the handoff.", "inputSchema": {"type": "object", "required": ["confirmed", "objective", "summary"], "properties": {"confirmed": {"type": "boolean"}, "objective": {"type": "string"}, "summary": {"type": "string"}, "workstreams": {"type": "array", "items": {"type": "string"}}, "decisions": {"type": "array", "items": {"type": "string"}}, "open_questions": {"type": "array", "items": {"type": "string"}}, "next_prompt": {"type": "string"}}}},
        {"name": "connector_status", "description": "Inspect configured source connectors without exposing credentials.", "inputSchema": {"type": "object", "properties": {}}},
        {"name": "create_connector_draft", "description": "Draft a future connector plan; it cannot be enabled until tests and Andy approval are complete.", "inputSchema": {"type": "object", "required": ["name", "purpose"], "properties": {"name": {"type": "string"}, "purpose": {"type": "string"}, "requested_capabilities": {"type": "array", "items": {"type": "string"}}}}},
    ]


def _text(payload: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2, sort_keys=True)}]}


def call_tool(config: Config, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "sync_sources":
        return _text(sync_local_sources(config, paths=arguments.get("paths"), max_items=int(arguments.get("max_items", 20))))
    if name == "sync_google_drive":
        return _text(sync_google_drive(config, file_ids=arguments.get("file_ids"), query=arguments.get("query"), max_items=int(arguments.get("max_items", 20))))
    if name == "sync_notion":
        return _text(sync_notion(config, page_ids=arguments.get("page_ids"), query=arguments.get("query"), max_items=int(arguments.get("max_items", 20))))
    if name == "get_command_center":
        path = vault_path(config, "00 Command Center/Home.md")
        return _text({"path": str(path), "markdown": path.read_text(encoding="utf-8") if path.exists() else "Command Center has not been published yet."})
    if name == "list_proposals":
        return _text({"proposals": list_proposals(config)})
    if name == "propose_workstream_update":
        return _text(propose_workstream_update(config, **arguments))
    if name == "propose_priority_override":
        return _text(propose_priority_override(config, **arguments))
    if name == "propose_research_update":
        return _text(propose_research_update(config, **arguments))
    if name == "propose_external_write":
        return _text(propose_external_write(config, **arguments))
    if name == "propose_system_change":
        return _text(propose_system_change(config, **arguments))
    if name == "propose_presentation_change":
        return _text(propose_presentation_change(config, arguments["summary"], arguments["profile_updates"], arguments.get("migration")))
    if name == "apply_proposal":
        proposal = apply_proposal(config, arguments["proposal_id"], bool(arguments["confirmed"]))
        publish_command_center(config, load_runtime(config))
        return _text(proposal)
    if name == "save_chat_handoff":
        if not arguments.get("confirmed"):
            raise PermissionError("Andy must explicitly confirm a chat handoff before it is written")
        changed = save_chat_handoff(
            config,
            objective=arguments["objective"],
            summary=arguments["summary"],
            workstreams=arguments.get("workstreams"),
            decisions=arguments.get("decisions"),
            open_questions=arguments.get("open_questions"),
            next_prompt=arguments.get("next_prompt", ""),
        )
        publish_command_center(config, load_runtime(config))
        return _text({"saved": changed})
    if name == "connector_status":
        return _text(connector_status(config))
    if name == "create_connector_draft":
        return _text(draft_connector(config, arguments["name"], arguments["purpose"], arguments.get("requested_capabilities", [])))
    raise KeyError(f"unknown tool: {name}")


def _response(identifier: Any, result: dict[str, Any] | None = None, error: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": identifier}
    if error is not None:
        payload["error"] = error
    else:
        payload["result"] = result or {}
    return payload


def handle_request(config: Config, request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    identifier = request.get("id")
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        return _response(identifier, {"protocolVersion": request.get("params", {}).get("protocolVersion", "2024-11-05"), "capabilities": {"tools": {}}, "serverInfo": SERVER_INFO})
    if method == "tools/list":
        return _response(identifier, {"tools": tool_definitions()})
    if method == "tools/call":
        params = request.get("params", {})
        try:
            return _response(identifier, call_tool(config, params["name"], params.get("arguments", {})))
        except Exception as exc:  # MCP requires a JSON-RPC error rather than a crashed stdio server.
            return _response(identifier, error={"code": -32000, "message": str(exc)})
    return _response(identifier, error={"code": -32601, "message": f"method not found: {method}"})


def serve(config: Config | None = None) -> int:
    active_config = config or load_config()
    for line in sys.stdin:
        try:
            request = json.loads(line)
            response = handle_request(active_config, request)
            if response is not None:
                print(json.dumps(response), flush=True)
        except Exception as exc:
            print(json.dumps({"jsonrpc": "2.0", "error": {"code": -32700, "message": str(exc)}}), flush=True)
    return 0
