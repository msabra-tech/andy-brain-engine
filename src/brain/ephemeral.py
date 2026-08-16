from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from .config import Config
from .hashing import sha256_file
from .state import read_json, write_json


TEXT_SUFFIXES = {".txt", ".md", ".json", ".csv", ".log"}
LEDGER_PATH = Path("data/state/source-ledger.json")
SOURCES_PATH = Path("config/sources.local.json")


def source_config(config: Config) -> dict[str, Any]:
    return read_json(config.engine / SOURCES_PATH, {"version": 1, "local_folders": [], "connectors": {}})


def _ledger(config: Config) -> dict[str, Any]:
    return read_json(config.engine / LEDGER_PATH, {"version": 1, "sources": {}})


def _write_ledger(config: Config, ledger: dict[str, Any]) -> None:
    write_json(config.engine / LEDGER_PATH, ledger)


def _allowed_roots(config: Config) -> list[Path]:
    roots: list[Path] = []
    for value in source_config(config).get("local_folders", []):
        try:
            root = Path(value).expanduser().resolve()
        except OSError:
            continue
        if root.exists() and root.is_dir():
            roots.append(root)
    return roots


def _is_allowed(path: Path, roots: list[Path]) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    for root in roots:
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _excerpt(path: Path, limit: int) -> str:
    if path.suffix.lower() not in TEXT_SUFFIXES:
        return f"[{path.suffix.lower() or 'binary'} file; text extraction is not configured]"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"[unreadable: {exc}]"
    normalized = "\n".join(line.rstrip() for line in text.splitlines())
    return normalized[:limit]


def sync_local_sources(
    config: Config,
    *,
    paths: list[str] | None = None,
    max_items: int = 20,
    excerpt_limit: int = 6000,
) -> dict[str, Any]:
    """Read current source material into the current tool response without persisting it."""
    roots = _allowed_roots(config)
    candidates: list[Path] = []
    if paths:
        for value in paths:
            path = Path(value).expanduser()
            if path.exists() and path.is_file() and _is_allowed(path, roots):
                candidates.append(path.resolve())
    else:
        for root in roots:
            candidates.extend(path for path in root.rglob("*") if path.is_file())
    ledger = _ledger(config)
    records: list[dict[str, Any]] = []
    for path in sorted(dict.fromkeys(candidates), key=lambda item: str(item).lower())[:max_items]:
        try:
            stat = path.stat()
            digest = sha256_file(path)
        except OSError:
            continue
        source_id = f"local-{digest[:16]}"
        locator = str(path)
        ledger.setdefault("sources", {})[source_id] = {
            "connector": "local_folder",
            "locator": locator,
            "name": path.name,
            "modified_at": dt.datetime.fromtimestamp(stat.st_mtime, dt.timezone.utc).isoformat(),
            "last_seen_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "sha256": digest,
        }
        records.append(
            {
                "source_id": source_id,
                "name": path.name,
                "locator": locator,
                "modified_at": ledger["sources"][source_id]["modified_at"],
                "excerpt": _excerpt(path, excerpt_limit),
            }
        )
    _write_ledger(config, ledger)
    return {
        "records": records,
        "temporary": True,
        "retention": "No source body was stored by the engine. The excerpts exist only in this tool response.",
        "configured_roots": [str(root) for root in roots],
    }


def draft_connector(config: Config, name: str, purpose: str, requested_capabilities: list[str]) -> dict[str, Any]:
    path = config.engine / "data/state/connector-drafts.json"
    state = read_json(path, {"version": 1, "drafts": []})
    draft = {
        "id": f"connector-{len(state.get('drafts', [])) + 1:04d}",
        "name": name.strip(),
        "purpose": purpose.strip(),
        "requested_capabilities": requested_capabilities,
        "status": "draft",
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "approval_required": True,
    }
    state.setdefault("drafts", []).append(draft)
    write_json(path, state)
    return draft
