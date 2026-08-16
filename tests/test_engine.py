from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from brain.command_center import publish_command_center
from brain.config import Config, RuntimeConfig
from brain.connectors import connector_status, sync_google_drive, sync_notion
from brain.ephemeral import sync_local_sources
from brain.mcp_server import handle_request
from brain.operations import (
    apply_proposal,
    list_proposals,
    propose_external_write,
    propose_presentation_change,
    propose_priority_override,
    propose_research_update,
    propose_system_change,
    propose_workstream_update,
    save_chat_handoff,
)
from brain.system_evolution import stage_system_change, validate_patch
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

    def test_windows_notification_surfaces_due_followups(self):
        with tempfile.TemporaryDirectory() as directory:
            config, runtime = make_env(Path(directory))
            proposal = propose_workstream_update(
                config,
                title="Michael Follow-up",
                summary="Keep the market question moving.",
                priority="High",
                follow_ups=[{"text": "Send Michael the research brief.", "due_date": "2000-01-01", "reason": "Meeting commitment"}],
            )
            apply_proposal(config, proposal["id"], confirmed=True)
            publish_command_center(config, runtime)
            summary = notification_summary(config)
            self.assertEqual(len(summary["deadline_items"]), 1)
            self.assertIn("due or overdue", summary["body"])

    def test_google_drive_sync_is_ephemeral_and_keeps_only_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            config, _ = make_env(Path(directory))

            def fake_json(method, url, headers, payload=None):
                self.assertEqual(method, "GET")
                self.assertIn("Bearer temporary-access-token", headers["Authorization"])
                self.assertIn("/files?", url)
                return {"files": [{"id": "drive-file-1", "name": "Michael market brief", "mimeType": "text/plain", "modifiedTime": "2026-08-16T10:00:00Z", "webViewLink": "https://drive.google.com/file/d/drive-file-1/view"}]}

            def fake_bytes(method, url, headers):
                self.assertIn("alt=media", url)
                return b"Private market research with a pricing lead."

            with patch("brain.connectors._google_access_token", return_value="temporary-access-token"):
                result = sync_google_drive(config, request_json=fake_json, request_bytes=fake_bytes)
            self.assertEqual(result["records"][0]["name"], "Michael market brief")
            self.assertIn("Private market research", result["records"][0]["excerpt"])
            ledger = (config.engine / "data/state/source-ledger.json").read_text(encoding="utf-8")
            self.assertNotIn("Private market research", ledger)
            self.assertIn("google_drive", ledger)

    def test_notion_sync_is_ephemeral_and_reads_enhanced_markdown(self):
        with tempfile.TemporaryDirectory() as directory:
            config, _ = make_env(Path(directory))

            def fake_json(method, url, headers, payload=None):
                self.assertEqual(headers["Notion-Version"], "2026-03-11")
                if url.endswith("/v1/search"):
                    self.assertEqual(method, "POST")
                    self.assertEqual(payload["filter"]["value"], "page")
                    return {"results": [{"id": "notion-page-1", "url": "https://www.notion.so/notion-page-1", "last_edited_time": "2026-08-16T10:00:00Z", "properties": {"Name": {"type": "title", "title": [{"plain_text": "Michael meeting notes"}]}}}]}
                self.assertTrue(url.endswith("/v1/pages/notion-page-1/markdown"))
                return {"markdown": "Confidential note: validate the new market before Friday."}

            with patch("brain.connectors._notion_headers", return_value={"Authorization": "Bearer temporary", "Notion-Version": "2026-03-11"}):
                result = sync_notion(config, request_json=fake_json)
            self.assertEqual(result["records"][0]["name"], "Michael meeting notes")
            self.assertIn("Confidential note", result["records"][0]["excerpt"])
            ledger = (config.engine / "data/state/source-ledger.json").read_text(encoding="utf-8")
            self.assertNotIn("Confidential note", ledger)
            self.assertIn("notion", ledger)

    def test_connector_status_never_exposes_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            config, _ = make_env(Path(directory))
            write_json(
                config.engine / "config/sources.local.json",
                {"version": 1, "local_folders": [], "connectors": {"google_drive": {"status": "connected", "access_token": "must-not-leak"}, "notion": {"status": "connected", "client_secret": "must-not-leak"}}},
            )
            status = connector_status(config)
            self.assertEqual(status["connectors"]["google_drive"]["mode"], "approval_required")
            self.assertNotIn("must-not-leak", json.dumps(status))

    def test_mcp_exposes_drive_and_notion_sync_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            config, _ = make_env(Path(directory))
            with patch("brain.mcp_server.sync_google_drive", return_value={"connector": "google_drive", "records": [], "temporary": True}) as drive_sync:
                response = handle_request(config, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "sync_google_drive", "arguments": {"query": "pricing"}}})
            self.assertEqual(json.loads(response["result"]["content"][0]["text"])["connector"], "google_drive")
            self.assertEqual(drive_sync.call_args.kwargs["query"], "pricing")
            names = {tool["name"] for tool in handle_request(config, {"jsonrpc": "2.0", "id": 4, "method": "tools/list"})["result"]["tools"]}
            self.assertTrue({"sync_google_drive", "sync_notion"}.issubset(names))

    def test_priority_score_reasoning_and_andy_override_are_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            config, runtime = make_env(Path(directory))
            proposal = propose_workstream_update(
                config,
                title="Expansion",
                summary="Michael asked for a market expansion recommendation.",
                priority="High",
                priority_score=84,
                priority_reasoning="Michael requested it and the pricing decision is due this week.",
            )
            apply_proposal(config, proposal["id"], confirmed=True)
            override = propose_priority_override(config, title="Expansion", priority="Critical", priority_score=96, reasoning="Andy must decide before Thursday.")
            apply_proposal(config, override["id"], confirmed=True)
            publish_command_center(config, runtime)
            command_center = (config.vault / "00 Command Center/Home.md").read_text(encoding="utf-8")
            overview = (config.vault / "10 Active Work/Expansion/Overview.md").read_text(encoding="utf-8")
            self.assertIn("96/100", command_center)
            self.assertIn("Andy must decide before Thursday", command_center)
            self.assertIn("andy_override: Critical", overview)

    def test_research_update_is_approved_and_attached_to_workstream(self):
        with tempfile.TemporaryDirectory() as directory:
            config, _ = make_env(Path(directory))
            proposal = propose_research_update(
                config,
                title="Market Validation",
                question="Is the proposed segment large enough?",
                findings="The reachable market is growing but supplier concentration is a risk.",
                evidence_links=["[Industry report](https://example.com/report)"],
                recommendation="Validate the top two suppliers before committing.",
                next_steps=["Ask Michael which segment matters most."],
            )
            with self.assertRaises(PermissionError):
                apply_proposal(config, proposal["id"], confirmed=False)
            apply_proposal(config, proposal["id"], confirmed=True)
            research = (config.vault / "10 Active Work/Market-Validation/Research.md").read_text(encoding="utf-8")
            self.assertIn("supplier concentration", research)
            self.assertIn("Ask Michael", research)

    def test_external_write_is_only_dispatched_after_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            config, _ = make_env(Path(directory))
            proposal = propose_external_write(config, connector="notion", title="Market brief", content="# Market brief\n\nApproved summary", target="parent-page")
            with self.assertRaises(PermissionError):
                apply_proposal(config, proposal["id"], confirmed=False)
            with patch("brain.connectors.write_external_artifact", return_value={"connector": "notion", "id": "new-page", "url": "https://notion.so/new-page"}) as write:
                applied = apply_proposal(config, proposal["id"], confirmed=True)
            self.assertEqual(write.call_count, 1)
            self.assertIn("https://notion.so/new-page", applied["changed"][0])

    def test_presentation_profile_changes_the_command_center_order(self):
        with tempfile.TemporaryDirectory() as directory:
            config, runtime = make_env(Path(directory))
            proposal = propose_presentation_change(config, "Move decisions first.", {"home_sections": ["decisions", "needs_attention", "active_work", "handoff"]})
            apply_proposal(config, proposal["id"], confirmed=True)
            publish_command_center(config, runtime)
            home = (config.vault / "00 Command Center/Home.md").read_text(encoding="utf-8")
            self.assertLess(home.index("## Decisions and Insights"), home.index("## Needs Attention"))

    def test_system_change_requires_a_standard_safe_diff(self):
        patch_text = """diff --git a/docs/New-Connector.md b/docs/New-Connector.md
new file mode 100644
--- /dev/null
+++ b/docs/New-Connector.md
@@ -0,0 +1 @@
+# New connector
"""
        self.assertEqual(validate_patch(patch_text), ["docs/New-Connector.md"])
        with self.assertRaises(ValueError):
            validate_patch("diff --git a/data/state/token.json b/data/state/token.json\n--- /dev/null\n+++ b/data/state/token.json\n@@ -0,0 +1 @@\n+bad\n")

    def test_system_change_stages_in_isolated_workspace_before_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            config, _ = make_env(Path(directory))
            patch_text = """diff --git a/docs/New-Connector.md b/docs/New-Connector.md
new file mode 100644
--- /dev/null
+++ b/docs/New-Connector.md
@@ -0,0 +1 @@
+# New connector
"""
            with patch("brain.system_evolution.stage_system_change", return_value={"passed": True, "workspace": "isolated", "affected_files": ["docs/New-Connector.md"], "checks": []}):
                proposal = propose_system_change(config, title="Add connector notes", summary="Add safe connector documentation.", patch=patch_text)
            self.assertEqual(proposal["kind"], "system_change")
            self.assertTrue(proposal["payload"]["preflight"]["passed"])

    def test_system_change_preflight_never_changes_live_engine(self):
        patch_text = """diff --git a/docs/Preview.md b/docs/Preview.md
new file mode 100644
--- /dev/null
+++ b/docs/Preview.md
@@ -0,0 +1 @@
+# Preview only
"""
        with tempfile.TemporaryDirectory() as directory:
            config, _ = make_env(Path(directory))
            preflight = stage_system_change(config, "proposal-0001", patch_text)
            self.assertTrue(preflight["patch_applied_in_workspace"])
            self.assertTrue((Path(preflight["workspace"]) / "docs/Preview.md").exists())
            self.assertFalse((config.engine / "docs/Preview.md").exists())


if __name__ == "__main__":
    unittest.main()
