from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from brain.command_center import publish_command_center
from brain.config import Config, RuntimeConfig
from brain.ephemeral import sync_local_sources
from brain.mcp_server import handle_request
from brain.operations import apply_proposal, list_proposals, propose_presentation_change, propose_workstream_update, save_chat_handoff
from brain.state import write_json
from brain.validation import validate_all
from brain.windows_notifications import notification_summary, review_task_script


def make_env(root: Path) -> tuple[Config, RuntimeConfig]:
    engine = root / "engine"
    vault = root / "vault"
    staging = root / "staging"
    for path in [engine / "data/state", engine / "data/backups", engine / "config", vault, staging]:
        path.mkdir(parents=True, exist_ok=True)
    config = Config(engine, vault, staging)
    runtime = RuntimeConfig(owner_name="Andy", vault_title="Andy Brain", automatic_ingestion=False, automatic_reminders=False)
    publish_command_center(config, runtime)
    return config, runtime


class AndyBrainTests(unittest.TestCase):
    def test_command_center_is_the_refined_vault_home(self):
        with tempfile.TemporaryDirectory() as directory:
            config, _ = make_env(Path(directory))
            home = (config.vault / "00 Command Center/Home.md").read_text(encoding="utf-8")
            self.assertIn("Andy Brain Command Center", home)
            self.assertTrue((config.vault / "40 Chat Handoffs/Current Context.md").exists())
            self.assertEqual(validate_all(config), [])

    def test_workstream_update_requires_explicit_apply(self):
        with tempfile.TemporaryDirectory() as directory:
            config, runtime = make_env(Path(directory))
            proposal = propose_workstream_update(
                config,
                title="Market Expansion",
                summary="A new supplier note is relevant to Michael's market request.",
                priority="High",
                people=["Michael"],
                source_links=["[Supplier note](https://drive.google.com/example)"],
                open_threads=["Confirm supplier pricing assumptions."],
                research="Validate supplier concentration before making a recommendation.",
            )
            with self.assertRaises(PermissionError):
                apply_proposal(config, proposal["id"], confirmed=False)
            applied = apply_proposal(config, proposal["id"], confirmed=True)
            self.assertEqual(applied["status"], "applied")
            publish_command_center(config, runtime)
            overview = config.vault / "10 Active Work/Market-Expansion/Overview.md"
            self.assertTrue(overview.exists())
            self.assertIn("Market Expansion", (config.vault / "00 Command Center/Home.md").read_text(encoding="utf-8"))
            self.assertIn("Confirm supplier pricing", (config.vault / "10 Active Work/Market-Expansion/Open Threads.md").read_text(encoding="utf-8"))
            self.assertEqual(list_proposals(config), [])

    def test_handoff_is_refined_context_not_a_raw_transcript(self):
        with tempfile.TemporaryDirectory() as directory:
            config, _ = make_env(Path(directory))
            saved = save_chat_handoff(
                config,
                objective="Prepare for Michael's market review.",
                summary="Supplier concentration remains the key decision.",
                workstreams=["Market Expansion"],
                open_questions=["Does the new pricing file change the recommendation?"],
                next_prompt="Continue the Market Expansion research and validate the pricing assumption.",
            )
            current = (config.vault / "40 Chat Handoffs/Current Context.md").read_text(encoding="utf-8")
            self.assertIn("Prepare for Michael", current)
            self.assertIn("Continue the Market Expansion", current)
            self.assertEqual(len(saved), 2)
            self.assertTrue((config.vault / "40 Chat Handoffs/History").exists())

    def test_sync_reads_only_approved_local_source_and_persists_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, _ = make_env(root)
            source_root = root / "approved"
            source_root.mkdir()
            note = source_root / "market.txt"
            note.write_text("Sensitive raw working note for the current review.", encoding="utf-8")
            write_json(config.engine / "config/sources.local.json", {"version": 1, "local_folders": [str(source_root)], "connectors": {}})
            result = sync_local_sources(config)
            self.assertEqual(result["records"][0]["name"], "market.txt")
            self.assertIn("Sensitive raw", result["records"][0]["excerpt"])
            ledger = (config.engine / "data/state/source-ledger.json").read_text(encoding="utf-8")
            self.assertNotIn("Sensitive raw", ledger)
            self.assertFalse((config.engine / "data/raw").exists())

    def test_sync_rejects_a_path_outside_the_approved_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, _ = make_env(root)
            approved = root / "approved"
            approved.mkdir()
            outside = root / "outside.txt"
            outside.write_text("Do not read", encoding="utf-8")
            write_json(config.engine / "config/sources.local.json", {"version": 1, "local_folders": [str(approved)], "connectors": {}})
            result = sync_local_sources(config, paths=[str(outside)])
            self.assertEqual(result["records"], [])

    def test_presentation_changes_back_up_refined_vault_before_apply(self):
        with tempfile.TemporaryDirectory() as directory:
            config, _ = make_env(Path(directory))
            proposal = propose_presentation_change(config, "Put decisions before active work.", {"home_sections": ["needs_attention", "decisions", "active_work"]})
            apply_proposal(config, proposal["id"], confirmed=True)
            profile = json.loads((config.engine / "config/presentation.local.json").read_text(encoding="utf-8"))
            self.assertEqual(profile["home_sections"][1], "decisions")
            self.assertTrue(any((config.engine / "data/backups/vault").iterdir()))

    def test_mcp_requires_confirmation_for_vault_apply(self):
        with tempfile.TemporaryDirectory() as directory:
            config, _ = make_env(Path(directory))
            proposed = handle_request(config, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "propose_workstream_update", "arguments": {"title": "Hiring", "summary": "Michael asked for a hiring plan."}}})
            content = json.loads(proposed["result"]["content"][0]["text"])
            refused = handle_request(config, {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "apply_proposal", "arguments": {"proposal_id": content["id"], "confirmed": False}}})
            self.assertIn("confirmation", refused["error"]["message"])

    def test_validator_rejects_raw_retention(self):
        with tempfile.TemporaryDirectory() as directory:
            config, _ = make_env(Path(directory))
            raw = config.engine / "data/raw"
            raw.mkdir(parents=True)
            (raw / "source.txt").write_text("raw", encoding="utf-8")
            self.assertTrue(any("raw source retention" in error for error in validate_all(config)))

    def test_windows_setup_dry_run_has_no_icloud_dependency(self):
        spec = importlib.util.spec_from_file_location("andy_setup", ROOT / "scripts/setup_windows.py")
        module = importlib.util.module_from_spec(spec)
        self.assertIsNotNone(spec.loader)
        spec.loader.exec_module(module)
        self.assertEqual(module.main(["--dry-run", "--owner-name", "Andy", "--vault-title", "Andy Brain", "--yes"]), 0)

    def test_windows_notification_scripts_read_the_refined_command_center(self):
        with tempfile.TemporaryDirectory() as directory:
            config, runtime = make_env(Path(directory))
            proposal = propose_workstream_update(config, title="Pricing", summary="Pricing deadline is tomorrow.", priority="High")
            apply_proposal(config, proposal["id"], confirmed=True)
            publish_command_center(config, runtime)
            summary = notification_summary(config)
            self.assertIn("need attention", summary["body"])
            script = review_task_script(config, runtime)
            self.assertIn("notifications summary", script.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
