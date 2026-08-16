#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path


BRIDGE_DIRS = [
    "inbox/mobile",
    "inbox/text",
    "inbox/files",
    "inbox/audio",
    "inbox/apple-context",
    "outbox/reminders",
    "receipts/ingestion",
    "receipts/reminders",
    "errors",
    "archive/demo-fixtures",
]

ENGINE_DIRS = [
    "data/raw",
    "data/state",
    "data/logs/runner",
    "data/locks",
    "data/backups",
]


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo = Path(__file__).resolve().parents[1]
    home = Path.home()
    mobile_documents = home / "Library/Mobile Documents"
    obsidian_parent = mobile_documents / "iCloud~md~obsidian/Documents"
    icloud_drive = mobile_documents / "com~apple~CloudDocs"

    if not mobile_documents.exists() and not args.dry_run:
        fail("iCloud Drive does not appear to be enabled. Enable iCloud Drive, open Obsidian once, then rerun setup.")

    owner_name = args.owner_name or prompt("Owner name", os.environ.get("USER", "Owner").replace(".", " ").title(), args)
    vault_title = args.vault_title or prompt("Vault title", f"{owner_name} Brain", args)
    vault_name = args.vault_name or prompt("iCloud Obsidian vault folder", slugify(vault_title), args)
    bridge_name = args.bridge_name or prompt("iCloud bridge folder", f"{slugify(vault_title)}-Bridge", args)
    timezone = args.timezone or prompt("Default timezone", os.environ.get("TZ", "UTC"), args)

    plaud = collect_plaud(args)

    vault = obsidian_parent / vault_name
    bridge = icloud_drive / bridge_name

    replacements = {
        "OWNER_NAME": owner_name,
        "VAULT_TITLE": vault_title,
        "VAULT_NAME": vault_name,
        "BRIDGE_NAME": bridge_name,
        "TODAY": date.today().isoformat(),
        "TIMEZONE": timezone,
        "PLAUD_STATUS_LINE": plaud["vault_status_line"],
    }

    say("Planned local layout")
    say(f"  Engine: {repo}")
    say(f"  Vault:  {vault}")
    say(f"  Bridge: {bridge}")

    if args.dry_run:
        say("Dry run only. No files will be written.")
        return 0

    obsidian_parent.mkdir(parents=True, exist_ok=True)
    icloud_drive.mkdir(parents=True, exist_ok=True)
    vault.mkdir(parents=True, exist_ok=True)
    bridge.mkdir(parents=True, exist_ok=True)

    for rel in ENGINE_DIRS:
        ensure_dir(repo / rel)
    for rel in BRIDGE_DIRS:
        ensure_dir(bridge / rel)

    render_vault(repo / "templates/vault", vault, replacements, force=args.force)
    write_json(repo / "config/paths.local.json", {
        "engine": str(repo),
        "vault": str(vault),
        "bridge": str(bridge),
    })
    write_json(repo / "config/runtime.local.json", runtime_payload(args, owner_name, vault_title, timezone, plaud))
    write_json(repo / "config/integrations.local.json", {"version": 1, "plaud": plaud["config"]})
    write_json(bridge / "outbox/reminders/actions.json", {"version": 1, "generated_at": None, "actions": []}, overwrite=False)

    run([str(repo / "brain"), "publish"], repo, "Publishing Home.md and Today.md")
    run([str(repo / "brain"), "verify"], repo, "Verifying vault boundary")

    should_install_runner = args.install_runner
    if not args.skip_runner and not should_install_runner and interactive(args):
        should_install_runner = ask_yes_no("Install the background inbox runner now?", default=True)
    if should_install_runner and not args.skip_runner:
        run([str(repo / "brain"), "runner", "install"], repo, "Installing LaunchAgent runner")

    say("")
    say("Setup complete.")
    say(f"Open Obsidian on the iPhone and choose the iCloud vault named: {vault_name}")
    say(f"Drop transcripts into: {bridge / 'inbox/mobile'}")
    say("Run './brain status' any time to inspect the setup.")
    return 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Set up a local iCloud Obsidian second-brain vault.")
    parser.add_argument("--owner-name")
    parser.add_argument("--vault-title")
    parser.add_argument("--vault-name")
    parser.add_argument("--bridge-name")
    parser.add_argument("--timezone")
    parser.add_argument("--yes", action="store_true", help="Use defaults without prompting where possible.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="Overwrite existing template files in the vault.")
    parser.add_argument("--install-runner", action="store_true")
    parser.add_argument("--skip-runner", action="store_true")
    parser.add_argument("--plaud-mcp-command", default="")
    parser.add_argument("--plaud-configured", action="store_true")
    return parser.parse_args(argv)


def runtime_payload(args: argparse.Namespace, owner_name: str, vault_title: str, timezone: str, plaud: dict) -> dict:
    return {
        "automatic_ingestion": True,
        "automatic_publishing": True,
        "automatic_reminders": False,
        "owner_name": owner_name,
        "vault_title": vault_title,
        "reminder_list": vault_title,
        "scan_interval_seconds": 60,
        "stable_wait_seconds": 5,
        "default_timezone": timezone,
        "codex_executable": shutil.which("codex") or "/Applications/Codex.app/Contents/Resources/codex",
        "python3_executable": shutil.which("python3") or sys.executable,
        "shortcuts_executable": shutil.which("shortcuts") or "/usr/bin/shortcuts",
        "osascript_executable": shutil.which("osascript") or "/usr/bin/osascript",
        "codex_enabled": True,
        "plaud_mcp_status": plaud["config"]["status"],
        "plaud_mcp_command": plaud["config"]["command"],
        "plaud_mcp_notes": plaud["config"]["notes"],
        "runner_label": f"com.personalbrain.{slugify(vault_title).lower()}.inbox-runner",
        "runner_wrapper_name": f"{slugify(vault_title).lower()}-inbox-runner",
    }


def collect_plaud(args: argparse.Namespace) -> dict:
    say("")
    say("PLAUD MCP integration")
    say("Do not paste PLAUD passwords, API keys, or private tokens into this setup.")
    command = args.plaud_mcp_command.strip()
    configured = args.plaud_configured
    if not configured and interactive(args):
        configured = ask_yes_no("Is a PLAUD MCP server already installed or ready to connect?", default=False)
    if configured and not command and interactive(args):
        command = input("PLAUD MCP command/server label (non-secret, optional): ").strip()
    status = "configured_pending_validation" if configured else "pending_user_authorization"
    notes = "Validate with a real exported transcript before relying on automated PLAUD import." if configured else "Connect only after the owner explicitly authorizes PLAUD access."
    vault_line = (
        "- PLAUD MCP status: configured locally, pending validation with a real export."
        if configured
        else "- PLAUD MCP status: pending owner authorization."
    )
    return {
        "config": {
            "status": status,
            "command": command,
            "notes": notes,
        },
        "vault_status_line": vault_line,
    }


def render_vault(template_root: Path, vault: Path, replacements: dict[str, str], force: bool) -> None:
    for src in sorted(template_root.rglob("*")):
        rel = render_relpath(src.relative_to(template_root), replacements)
        dst = vault / rel
        if src.is_dir():
            ensure_dir(dst)
            continue
        text = src.read_text(encoding="utf-8")
        for key, value in replacements.items():
            text = text.replace("{{" + key + "}}", value)
        if dst.exists() and not force:
            continue
        write_text(dst, text)


def render_relpath(path: Path, replacements: dict[str, str]) -> Path:
    rendered_parts = []
    for part in path.parts:
        rendered = part
        for key, value in replacements.items():
            rendered = rendered.replace("{{" + key + "}}", value)
        rendered_parts.append(rendered)
    return Path(*rendered_parts)


def prompt(label: str, default: str, args: argparse.Namespace) -> str:
    if args.yes or not sys.stdin.isatty():
        return default
    value = input(f"{label} [{default}]: ").strip()
    return value or default


def ask_yes_no(label: str, default: bool) -> bool:
    suffix = "Y/n" if default else "y/N"
    value = input(f"{label} [{suffix}]: ").strip().lower()
    if not value:
        return default
    return value in {"y", "yes", "true", "1"}


def interactive(args: argparse.Namespace) -> bool:
    return not args.yes and sys.stdin.isatty()


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-")
    return slug or "Personal-Brain"


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_text(path: Path, text: str, overwrite: bool = True) -> None:
    if path.exists() and not overwrite:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload: dict, overwrite: bool = True) -> None:
    write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n", overwrite=overwrite)


def run(command: list[str], cwd: Path, label: str) -> None:
    say(label)
    proc = subprocess.run(command, cwd=cwd, text=True)
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)


def say(message: str) -> None:
    print(message)


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


if __name__ == "__main__":
    raise SystemExit(main())
