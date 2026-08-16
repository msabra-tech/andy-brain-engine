from __future__ import annotations

from .config import Config, RuntimeConfig
from .connectors import connector_status
from .operations import list_proposals
from .state import read_json


def build_status(config: Config, runtime: RuntimeConfig, test_codex: bool = True) -> dict[str, object]:
    """Return Andy Brain health without inspecting or retaining source content."""
    handoff = config.vault / "40 Chat Handoffs/Current Context.md"
    command_center = config.vault / "00 Command Center/Home.md"
    ledger = read_json(config.engine / "data/state/source-ledger.json", {"sources": {}})
    return {
        "engine_path": str(config.engine),
        "vault_path": str(config.vault),
        "staging_path": str(config.bridge),
        "command_center": str(command_center),
        "command_center_exists": command_center.exists(),
        "chat_handoff_exists": handoff.exists(),
        "source_metadata_count": len(ledger.get("sources", {})),
        "pending_proposals": len(list_proposals(config)),
        "connectors": connector_status(config),
        "raw_source_retention": False,
        "daily_review_mode": "Claude scheduled draft plus Windows notification bridge",
    }
