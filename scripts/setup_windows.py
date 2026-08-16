#!/usr/bin/env python3
"""Create a Windows-local Andy Brain vault without copying source material."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path


ENGINE_DIRS = ["data/state", "data/logs", "data/locks", "data/backups", "data/workspaces"]
STAGING_DIRS = ["incoming", "outgoing", "errors"]


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-")
    return slug or "Andy-Brain"


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def render_vault(template: Path, vault: Path, replacements: dict[str, str], force: bool = False) -> None:
    for source in template.rglob("*"):
        relative = source.relative_to(template)
        target = vault / relative
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if target.exists() and not force:
            continue
        text = source.read_text(encoding="utf-8")
        for key, value in replacements.items():
            text = text.replace(f"{{{{{key}}}}}", value)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Set up Andy Brain on a Windows-local Obsidian vault.")
    parser.add_argument("--owner-name", default="Andy")
    parser.add_argument("--vault-title", default="Andy Brain")
    parser.add_argument("--vault-path")
    parser.add_argument("--staging-path")
    parser.add_argument("--output-path")
    parser.add_argument("--local-folder", action="append", default=[])
    parser.add_argument("--timezone", default=os.environ.get("TZ", "UTC"))
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo = Path(__file__).resolve().parents[1]
    default_documents = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Documents"
    vault = Path(args.vault_path).expanduser() if args.vault_path else default_documents / slugify(args.vault_title)
    staging = Path(args.staging_path).expanduser() if args.staging_path else repo / "data/staging"
    output = Path(args.output_path).expanduser() if args.output_path else default_documents / f"{slugify(args.vault_title)} Exports"
    print("Planned Andy Brain layout")
    print(f"  Engine:  {repo}")
    print(f"  Vault:   {vault}")
    print(f"  Staging: {staging}")
    print(f"  Output:  {output}")
    if args.dry_run:
        print("Dry run only. No files will be written.")
        return 0

    vault.mkdir(parents=True, exist_ok=True)
    staging.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    for relative in ENGINE_DIRS:
        (repo / relative).mkdir(parents=True, exist_ok=True)
    for relative in STAGING_DIRS:
        (staging / relative).mkdir(parents=True, exist_ok=True)
    render_vault(
        repo / "templates/andy-vault",
        vault,
        {"OWNER_NAME": args.owner_name, "VAULT_TITLE": args.vault_title, "TODAY": date.today().isoformat(), "TIMEZONE": args.timezone},
        force=args.force,
    )
    write_json(repo / "config/paths.local.json", {"engine": str(repo), "vault": str(vault), "bridge": str(staging)})
    write_json(
        repo / "config/runtime.local.json",
        {
            "owner_name": args.owner_name,
            "vault_title": args.vault_title,
            "default_timezone": args.timezone,
            "automatic_ingestion": False,
            "automatic_publishing": True,
            "automatic_reminders": False,
            "claude_desktop_enabled": True,
            "review_time": "09:00",
        },
    )
    write_json(
        repo / "config/sources.local.json",
        {
            "version": 1,
            "local_folders": [str(Path(folder).expanduser()) for folder in args.local_folder],
            "local_output_folder": str(output),
            "connectors": {
                "google_drive": {"status": "not_connected", "mode": "approval_required"},
                "notion": {"status": "not_connected", "mode": "approval_required"},
            },
        },
    )
    from pathlib import Path as _Path
    sys.path.insert(0, str(repo / "src"))
    from brain.command_center import publish_command_center
    from brain.config import Config, load_runtime
    from brain.validation import validate_all
    config = Config(repo, vault, staging)
    publish_command_center(config, load_runtime(config))
    errors = validate_all(config)
    if errors:
        print("Setup validation failed:", *errors, sep="\n- ")
        return 1
    print("Setup complete. Open the vault in Obsidian, then use Claude Desktop to run an Andy Brain review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
