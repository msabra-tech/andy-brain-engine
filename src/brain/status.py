from __future__ import annotations

import json
from pathlib import Path
from .codex_integration import codex_auth_check
from .config import Config, RuntimeConfig
from .hashing import sha256_file
from .reminders import detect_adapter, status as reminder_status
from .runner import runner_status
from .state import read_json

TEMP_SUFFIXES = {".tmp", ".part", ".download", ".crdownload", ".icloud"}


def build_status(config: Config, runtime: RuntimeConfig, test_codex: bool = True) -> dict[str, object]:
    processing = read_json(config.engine / "data/state/processing.json", {})
    actions = read_json(config.bridge / "outbox/reminders/actions.json", {"actions": []})
    receipts = _files_by_mtime(config.bridge / "receipts/ingestion")
    review_count = _review_count(config.vault)
    pending = _pending_count(config.bridge, processing)
    runner = runner_status(config, runtime)
    codex = codex_auth_check(config, runtime) if test_codex else {"ok": None, "skipped": True}
    return {
        "engine_path": str(config.engine),
        "vault_path": str(config.vault),
        "bridge_path": str(config.bridge),
        "runner_installed": runner.get("installed"),
        "runner_loaded": runner.get("loaded"),
        "runner_last_run": processing.get("last_run"),
        "runner_last_success": processing.get("last_success"),
        "runner_last_error": processing.get("last_error"),
        "codex_executable": runtime.codex_executable,
        "codex_authentication_test": codex.get("ok"),
        "inbox_pending_count": pending,
        "processing_count": len(processing.get("sources", {})),
        "review_count": review_count,
        "reminder_mode": "automatic-live" if runtime.automatic_reminders else "manual-or-dry-run",
        "reminder_adapter": detect_adapter(runtime).get("selected_adapter"),
        "reminder_queue_count": _planned_action_count(actions),
        "reminder_total_action_count": len(actions.get("actions", [])),
        "plaud_mcp_status": runtime.plaud_mcp_status,
        "last_reminder_receipt": _last_name(config.bridge / "receipts/reminders"),
        "last_ingestion_receipt": receipts[-1].name if receipts else None,
        "obsidian_publish_time": processing.get("last_success"),
    }


def _pending_count(bridge: Path, processing: dict) -> int:
    root = bridge / "inbox"
    if not root.exists():
        return 0
    successful = {
        digest
        for digest, record in processing.get("sources", {}).items()
        if isinstance(record, dict) and record.get("status") == "success"
    }
    count = 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part.startswith(".") for part in rel.parts):
            continue
        if path.name.endswith(".manifest.json") or path.suffix.lower() in TEMP_SUFFIXES:
            continue
        try:
            digest = sha256_file(path)
        except OSError:
            count += 1
            continue
        if digest not in successful:
            count += 1
    return count


def _review_count(vault: Path) -> int:
    path = vault / "References/Open Questions.md"
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("- "))


def _last_name(folder: Path) -> str | None:
    files = _files_by_mtime(folder)
    return files[-1].name if files else None


def _files_by_mtime(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return sorted(folder.glob("*.json"), key=lambda path: path.stat().st_mtime_ns)


def _planned_action_count(actions: dict) -> int:
    return sum(1 for action in actions.get("actions", []) if action.get("status") == "planned")
