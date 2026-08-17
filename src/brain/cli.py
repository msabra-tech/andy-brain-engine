from __future__ import annotations

import argparse
import getpass
import json

from .command_center import publish_command_center
from .config import load_config, load_runtime
from .connectors import authorize_google_drive, configure_notion_token, import_google_drive_client, sync_google_drive, sync_notion
from .ephemeral import sync_local_sources
from .lock import BrainLock, BrainLockActive
from .operations import apply_proposal, list_proposals
from .state import write_json
from .status import build_status
from .validation import validate_all


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="brain", description="Andy Brain local engine")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("publish", help="Render the Obsidian Command Center from refined knowledge.")
    sub.add_parser("verify", help="Validate vault boundaries and no-raw-retention rules.")
    sub.add_parser("status", help="Show connector, proposal, and Command Center health.")
    sub.add_parser("run-once", help="Refresh source metadata and render the Command Center without Claude writes.")
    sub.add_parser("mcp", help="Run the private Claude Desktop MCP server over stdio.")

    sync = sub.add_parser("sync", help="Return temporary excerpts from approved local sources.")
    sync.add_argument("paths", nargs="*")
    sync.add_argument("--max-items", type=int, default=20)
    sync.add_argument("--connector", choices=["local", "google-drive", "notion"], default="local")
    sync.add_argument("--query")

    connectors = sub.add_parser("connectors", help="Configure or authorize read-only source connectors on Andy's Windows machine.")
    connector_sub = connectors.add_subparsers(dest="connector_name", required=True)
    google_drive = connector_sub.add_parser("google-drive", help="Configure the Google Drive read-only connector.")
    google_drive_sub = google_drive.add_subparsers(dest="connector_action", required=True)
    import_client = google_drive_sub.add_parser("import-client", help="Securely import a Google Desktop OAuth client JSON file.")
    import_client.add_argument("client_file")
    authorize_google = google_drive_sub.add_parser("authorize", help="Open the browser consent flow for Drive read-only access.")
    authorize_google.add_argument("--timeout", type=int, default=300)
    notion = connector_sub.add_parser("notion", help="Configure the Notion read-only connector.")
    notion_sub = notion.add_subparsers(dest="connector_action", required=True)
    notion_sub.add_parser("authorize", help="Prompt securely for Andy's Notion Personal Access Token.")

    proposals = sub.add_parser("proposals", help="Inspect or apply Claude-proposed changes.")
    proposal_sub = proposals.add_subparsers(dest="proposal_command", required=True)
    proposal_list = proposal_sub.add_parser("list")
    proposal_list.add_argument("--all", action="store_true")
    proposal_apply = proposal_sub.add_parser("apply")
    proposal_apply.add_argument("proposal_id")
    proposal_apply.add_argument("--confirm", action="store_true")

    notifications = sub.add_parser("notifications", help="Generate and manage Windows review notifications.")
    notification_sub = notifications.add_subparsers(dest="notification_command", required=True)
    notification_sub.add_parser("summary")
    notification_sub.add_parser("send")
    install_notification = notification_sub.add_parser("install")
    install_notification.add_argument("--time", default="09:00")

    args = parser.parse_args(argv)
    config = load_config()
    runtime = load_runtime(config)

    if args.command == "publish":
        publish_command_center(config, runtime)
        return _print({"published": True, "command_center": str(config.vault / "00 Command Center/Home.md")})
    if args.command == "verify":
        errors = validate_all(config)
        if errors:
            return _print({"ok": False, "errors": errors}, 1)
        return _print({"ok": True})
    if args.command == "status":
        return _print(build_status(config, runtime))
    if args.command == "sync":
        if args.connector == "google-drive":
            return _print(sync_google_drive(config, file_ids=args.paths or None, query=args.query, max_items=args.max_items))
        if args.connector == "notion":
            return _print(sync_notion(config, page_ids=args.paths or None, query=args.query, max_items=args.max_items))
        return _print(sync_local_sources(config, paths=args.paths or None, max_items=args.max_items))
    if args.command == "mcp":
        from .mcp_server import serve
        return serve(config)
    if args.command == "run-once":
        return _run_once(config, runtime)
    if args.command == "proposals":
        if args.proposal_command == "list":
            return _print({"proposals": list_proposals(config, include_closed=args.all)})
        try:
            proposal = apply_proposal(config, args.proposal_id, args.confirm)
        except (KeyError, ValueError, PermissionError) as exc:
            return _print({"applied": False, "error": str(exc)}, 1)
        publish_command_center(config, runtime)
        return _print({"applied": True, "proposal": proposal})
    if args.command == "notifications":
        from .windows_notifications import install_schedule, notification_summary, send_notification
        if args.notification_command == "summary":
            return _print(notification_summary(config))
        if args.notification_command == "send":
            return _print(send_notification(config))
        return _print(install_schedule(config, runtime, args.time))
    if args.command == "connectors":
        if args.connector_name == "google-drive":
            if args.connector_action == "import-client":
                return _print(import_google_drive_client(config, args.client_file))
            return _print(authorize_google_drive(config, timeout_seconds=args.timeout))
        token = getpass.getpass("Paste Andy's Notion Personal Access Token (input is hidden): ")
        return _print(configure_notion_token(config, token))
    return 2


def _run_once(config, runtime) -> int:
    try:
        lock = BrainLock(config.engine / "data/locks/run-once.lock")
        lock.__enter__()
    except BrainLockActive as exc:
        return _print({"ok": False, "skipped_due_lock": True, "error": str(exc)}, 75)
    try:
        sync = sync_local_sources(config)
        publish_command_center(config, runtime)
        errors = validate_all(config)
        payload = {
            "ok": not errors,
            "sources_checked": len(sync.get("records", [])),
            "raw_retained": False,
            "errors": errors,
        }
        write_json(config.engine / "data/state/last-run.json", payload)
        return _print(payload, 1 if errors else 0)
    finally:
        lock.__exit__(None, None, None)


def _print(payload: dict, exit_code: int = 0) -> int:
    print(json.dumps(payload, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
