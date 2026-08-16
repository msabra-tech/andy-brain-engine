from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from zoneinfo import ZoneInfo
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from brain.config import Config, RuntimeConfig, is_relative_to
from brain.fixtures import create_dynamic_fixture
from brain.hashing import hash_text, sha256_file
from brain.ingest import ingest, scan_inbox, snapshots_match
from brain.lock import BrainLock
from brain.metadata import parse_capture
from brain.publish import publish
from brain.reminders import apply_reminders, plan_test_reminder, validate_actions
from brain.runner import write_plist, write_wrapper
from brain.validation import validate_all


def make_env(tmp: Path) -> tuple[Config, RuntimeConfig]:
    engine = tmp / "engine"
    vault = tmp / "vault"
    bridge = tmp / "bridge"
    for path in [
        engine / "data/state", engine / "data/raw", engine / "data/locks", engine / "data/logs/runner", engine / "scripts/runner", engine / "launchd",
        vault / ".obsidian", vault / "Projects", vault / "Ideas", vault / "Life", vault / "People", vault / "Work", vault / "Career", vault / "References/Sources",
        bridge / "inbox/mobile", bridge / "inbox/text", bridge / "inbox/files", bridge / "inbox/audio", bridge / "inbox/apple-context", bridge / "outbox/reminders", bridge / "receipts/ingestion", bridge / "receipts/reminders", bridge / "errors",
    ]:
        path.mkdir(parents=True, exist_ok=True)
    (vault / "Welcome.md").write_text("# Welcome\n[[Home]]\n", encoding="utf-8")
    (vault / "Quick Capture.md").write_text("# Quick Capture\n", encoding="utf-8")
    (vault / "References/Source Notes.md").write_text("# Source Notes\n", encoding="utf-8")
    (vault / "References/Open Questions.md").write_text("# Open Questions\n", encoding="utf-8")
    (vault / "References/Sources/Test Source.md").write_text("# Test Source\n", encoding="utf-8")
    (vault / "Projects/AI Second Brain.md").write_text("# AI Second Brain\n\nSource: [[References/Sources/Test Source]].\n", encoding="utf-8")
    (vault / "Projects/Active Projects.md").write_text("# Active Projects\n\nSource boundary: active projects only.\n", encoding="utf-8")
    (vault / "Ideas/Personal AI Second Brain.md").write_text("# Personal AI Second Brain\n\nSource: [[References/Sources/Test Source]].\n", encoding="utf-8")
    (vault / "Ideas/Ideas Index.md").write_text("# Ideas Index\n\nSource boundary: index.\n", encoding="utf-8")
    (vault / "Life/Tasks.md").write_text("# Tasks\n\n- [ ] Existing task. Source: [[References/Sources/Test Source]].\n", encoding="utf-8")
    (vault / "Life/Reminders.md").write_text("# Reminders\n\nSource boundary: none captured yet.\n", encoding="utf-8")
    (vault / "Life/Shopping.md").write_text("# Shopping\n\nSource boundary: none captured yet.\n", encoding="utf-8")
    (vault / "Life/Decisions.md").write_text("# Decisions\n\nSource boundary: none captured yet.\n", encoding="utf-8")
    (vault / "Life/Reflections.md").write_text("# Reflections\n\nSource: [[References/Sources/Test Source]].\n", encoding="utf-8")
    (vault / "Work/Work Notes.md").write_text("# Work Notes\n\nSource boundary: none captured yet.\n", encoding="utf-8")
    (vault / "Career/Career Notes.md").write_text("# Career Notes\n\nSource boundary: none captured yet.\n", encoding="utf-8")
    (vault / "People/People Index.md").write_text("# People\n", encoding="utf-8")
    cfg = Config(engine, vault, bridge)
    runtime = RuntimeConfig(
        automatic_ingestion=True,
        automatic_publishing=True,
        automatic_reminders=False,
        stable_wait_seconds=0,
        default_timezone="Africa/Cairo",
        codex_enabled=False,
        python3_executable=sys.executable,
        codex_executable="/bin/echo",
        shortcuts_executable="/usr/bin/false",
        osascript_executable="/usr/bin/false",
    )
    publish(cfg, runtime)
    return cfg, runtime


class EngineLoopTests(unittest.TestCase):
    def test_setup_template_renders_owner_vault(self):
        spec = importlib.util.spec_from_file_location("brain_setup", ROOT / "scripts/setup.py")
        setup = importlib.util.module_from_spec(spec)
        self.assertIsNotNone(spec.loader)
        spec.loader.exec_module(setup)
        with tempfile.TemporaryDirectory() as d:
            vault = Path(d) / "vault"
            setup.render_vault(ROOT / "templates/vault", vault, {
                "OWNER_NAME": "Example Owner",
                "VAULT_TITLE": "Example Brain",
                "VAULT_NAME": "Example-Brain",
                "BRIDGE_NAME": "Example-Brain-Bridge",
                "TODAY": "2026-07-21",
                "TIMEZONE": "UTC",
                "PLAUD_STATUS_LINE": "- PLAUD MCP status: pending owner authorization.",
            }, force=True)
            self.assertTrue((vault / "People/Example Owner.md").exists())
            self.assertIn("# Example Brain", (vault / "Home.md").read_text(encoding="utf-8"))
            self.assertNotIn("{{OWNER_NAME}}", (vault / ".obsidian/workspace-mobile.json").read_text(encoding="utf-8"))
            json.loads((vault / ".obsidian/graph.json").read_text(encoding="utf-8"))

    def test_path_relative_detection(self):
        self.assertTrue(is_relative_to(Path("/a/b/c"), Path("/a/b")))
        self.assertFalse(is_relative_to(Path("/a/b"), Path("/a/b/c")))

    def test_hashing(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.txt"
            p.write_text("hello", encoding="utf-8")
            self.assertEqual(hash_text("hello"), sha256_file(p))

    def test_frontmatter_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "note.txt"
            p.write_text("---\ncaptured_at: 2026-07-20T18:30:00+03:00\ntimezone: Africa/Cairo\nsource_type: plaud-export\nsource_title: Test Note\n---\nBody\n", encoding="utf-8")
            meta, body = parse_capture(p, p.read_text(encoding="utf-8"), "Africa/Cairo")
            self.assertEqual(meta.timestamp_basis, "supplied")
            self.assertEqual(meta.temporal_confidence, "high")
            self.assertEqual(body.strip(), "Body")

    def test_filesystem_metadata_lowers_confidence(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "note.txt"
            p.write_text("Body\n", encoding="utf-8")
            meta, _ = parse_capture(p, p.read_text(encoding="utf-8"), "Africa/Cairo")
            self.assertEqual(meta.timestamp_basis, "filesystem-arrival")
            self.assertEqual(meta.temporal_confidence, "low")

    def test_stable_snapshot_helper(self):
        self.assertTrue(snapshots_match((10, 20), (10, 20)))
        self.assertFalse(snapshots_match((10, 20), (11, 20)))

    def test_scan_ignores_hidden_and_temp_files(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, runtime = make_env(Path(d))
            (cfg.bridge / "inbox/mobile/.hidden.txt").write_text("x", encoding="utf-8")
            (cfg.bridge / "inbox/mobile/file.tmp").write_text("x", encoding="utf-8")
            (cfg.bridge / "inbox/mobile/file.txt").write_text("x", encoding="utf-8")
            found = scan_inbox(cfg, runtime, wait=False)
            self.assertEqual([item.path.name for item in found], ["file.txt"])

    def test_ingest_defers_unreadable_icloud_file(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, runtime = make_env(Path(d))
            (cfg.bridge / "inbox/mobile/file.txt").write_text("x", encoding="utf-8")
            with mock.patch("brain.ingest.sha256_file", side_effect=OSError(11, "Resource deadlock avoided")):
                counts = ingest(cfg, runtime, invoke_codex=False, wait_for_stable=False)
            self.assertEqual(counts["processed"], 0)
            self.assertEqual(counts["unstable"], 1)

    def test_ingest_skips_temporarily_unreadable_quick_capture(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, runtime = make_env(Path(d))
            original_read_text = Path.read_text

            def flaky_read_text(path, *args, **kwargs):
                if path.name == "Quick Capture.md":
                    raise OSError(11, "Resource deadlock avoided")
                return original_read_text(path, *args, **kwargs)

            with mock.patch("pathlib.Path.read_text", flaky_read_text):
                counts = ingest(cfg, runtime, invoke_codex=False, wait_for_stable=False)
            self.assertEqual(counts["processed"], 0)

    def test_demo_fixture_stays_out_of_real_records(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, runtime = make_env(Path(d))
            create_dynamic_fixture(cfg, runtime, "e2e")
            counts = ingest(cfg, runtime, invoke_codex=False, wait_for_stable=False)
            self.assertEqual(counts["processed"], 1)
            self.assertFalse((cfg.vault / "Demo").exists())
            self.assertNotIn("Shampoo", (cfg.vault / "Life/Shopping.md").read_text(encoding="utf-8"))
            self.assertEqual(validate_actions(cfg), [])
            self.assertFalse((cfg.bridge / "outbox/reminders/actions.json").exists())

    def test_real_transcript_distributes_labeled_items(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, runtime = make_env(Path(d))
            due = (dt.datetime.now(ZoneInfo("Africa/Cairo")) + dt.timedelta(minutes=10)).replace(microsecond=0)
            (cfg.bridge / "inbox/mobile/plan.txt").write_text(f"""---
captured_at: 2026-07-20T18:30:00+03:00
timezone: Africa/Cairo
source_type: planning-transcript
source_title: Owner Planning Note
speaker: Owner
---

Task: prepare the sample dashboard before dinner
Work: capture one client follow-up as a work note
Career: save one portfolio idea for later
Project: expand the AI Second Brain project map
Person: ask which people notes should be tracked
Idea: make unresolved items visible on the dashboard
Reflection: the system should be honest about uncertain facts
Decision: keep private account connections disabled until authorized
Question: whether PLAUD export access is available tomorrow
Shopping list: add shampoo and soap.
Remind me at {due.isoformat()} to review the plan.
""", encoding="utf-8")
            counts = ingest(cfg, runtime, invoke_codex=False, wait_for_stable=False)
            self.assertEqual(counts["processed"], 1)
            self.assertIn("prepare the sample dashboard", (cfg.vault / "Life/Tasks.md").read_text(encoding="utf-8"))
            self.assertIn("Shampoo", (cfg.vault / "Life/Shopping.md").read_text(encoding="utf-8"))
            self.assertIn("client follow-up", (cfg.vault / "Work/Work Notes.md").read_text(encoding="utf-8"))
            self.assertIn("portfolio idea", (cfg.vault / "Career/Career Notes.md").read_text(encoding="utf-8"))
            self.assertIn("AI Second Brain project map", (cfg.vault / "Projects/Active Projects.md").read_text(encoding="utf-8"))
            self.assertIn("which people notes", (cfg.vault / "People/People Index.md").read_text(encoding="utf-8"))
            self.assertIn("unresolved items visible", (cfg.vault / "Ideas/Ideas Index.md").read_text(encoding="utf-8"))
            self.assertIn("honest about uncertain facts", (cfg.vault / "Life/Reflections.md").read_text(encoding="utf-8"))
            self.assertIn("private account connections disabled", (cfg.vault / "Life/Decisions.md").read_text(encoding="utf-8"))
            self.assertIn("PLAUD export access", (cfg.vault / "References/Open Questions.md").read_text(encoding="utf-8"))
            self.assertEqual(validate_actions(cfg), [])
            actions = json.loads((cfg.bridge / "outbox/reminders/actions.json").read_text(encoding="utf-8"))["actions"]
            self.assertEqual(len(actions), 1)
            self.assertEqual(actions[0]["approval"], "automatic")

    def test_processed_bridge_files_are_not_reported_as_pending(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, runtime = make_env(Path(d))
            create_dynamic_fixture(cfg, runtime, "e2e")
            ingest(cfg, runtime, invoke_codex=False, wait_for_stable=False)
            publish(cfg, runtime)
            home = (cfg.vault / "Home.md").read_text(encoding="utf-8")
            self.assertIn("New captures waiting: no.", home)

    def test_duplicate_fixture_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, runtime = make_env(Path(d))
            create_dynamic_fixture(cfg, runtime, "e2e")
            first = ingest(cfg, runtime, invoke_codex=False, wait_for_stable=False)
            second = ingest(cfg, runtime, invoke_codex=False, wait_for_stable=False)
            self.assertEqual(first["processed"], 1)
            self.assertEqual(second["skipped"], 1)
            self.assertFalse((cfg.bridge / "outbox/reminders/actions.json").exists())

    def test_unsupported_binary_archived_and_reviewed(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, runtime = make_env(Path(d))
            (cfg.bridge / "inbox/files/audio.m4a").write_bytes(b"fake")
            counts = ingest(cfg, runtime, invoke_codex=False, wait_for_stable=False)
            self.assertEqual(counts["processed"], 1)
            self.assertTrue(any((cfg.engine / "data/raw").rglob("*.m4a")))
            self.assertIn("awaits extraction", (cfg.vault / "References/Open Questions.md").read_text(encoding="utf-8"))

    def test_publish_adds_presentation_home_and_today(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, runtime = make_env(Path(d))
            publish(cfg, runtime)
            home = (cfg.vault / "Home.md").read_text(encoding="utf-8")
            self.assertIn("Start Here", home)
            self.assertIn("[[Projects/AI Second Brain|AI Second Brain project]]", home)
            self.assertNotIn("GENERATED by hagar-brain-engine", home)
            self.assertFalse((cfg.vault / "System").exists())

    def test_verify_fails_on_developer_file_in_vault(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, runtime = make_env(Path(d))
            (cfg.vault / "scripts").mkdir()
            (cfg.vault / "scripts/x.py").write_text("print(1)", encoding="utf-8")
            self.assertTrue(any("developer" in e for e in validate_all(cfg)))

    def test_verify_fails_on_broken_link(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, runtime = make_env(Path(d))
            (cfg.vault / "Projects/Broken.md").write_text("# Broken\n\n[[Missing Note]]\n\nSource: [[References/Sources/Test Source]].\n", encoding="utf-8")
            self.assertTrue(any("broken wikilink" in e for e in validate_all(cfg)))

    def test_bridge_inside_vault_fails(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, runtime = make_env(Path(d))
            bad = Config(cfg.engine, cfg.vault, cfg.vault / "Bridge")
            self.assertTrue(any("bridge is inside" in e for e in validate_all(bad)))

    def test_duplicate_source_hash_fails(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, runtime = make_env(Path(d))
            raw = cfg.engine / "data/raw/a.txt"
            raw.write_text("a", encoding="utf-8")
            digest = sha256_file(raw)
            (cfg.engine / "data/state/archive_manifest.json").write_text(json.dumps({"items": [{"path": "data/raw/a.txt", "sha256": digest}, {"path": "data/raw/a.txt", "sha256": digest}]}), encoding="utf-8")
            self.assertTrue(any("duplicate source hash" in e for e in validate_all(cfg)))

    def test_raw_archive_modified_fails(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, runtime = make_env(Path(d))
            raw = cfg.engine / "data/raw/file.txt"
            raw.write_text("a", encoding="utf-8")
            (cfg.engine / "data/state/archive_manifest.json").write_text(json.dumps({"items": [{"path": "data/raw/file.txt", "sha256": "wrong"}]}), encoding="utf-8")
            self.assertTrue(any("raw archive modified" in e for e in validate_all(cfg)))

    def test_reminder_plan_and_dry_run_receipt(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, runtime = make_env(Path(d))
            plan_test_reminder(cfg, runtime)
            result = apply_reminders(cfg, runtime, live=False)
            self.assertEqual(result["results"][0]["status"], "dry-run")

    def test_live_apply_skips_automatic_actions_when_mode_is_off(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, runtime = make_env(Path(d))
            due = (dt.datetime.now(ZoneInfo("Africa/Cairo")) + dt.timedelta(minutes=10)).isoformat()
            action = {"action_id": "auto-a", "operation": "create", "title": "automatic fixture", "due": due, "all_day": False, "list": "Personal Brain", "notes": "", "source_id": "s", "source_path": "p", "confidence": "high", "approval": "automatic", "status": "planned"}
            (cfg.bridge / "outbox/reminders/actions.json").write_text(json.dumps({"actions": [action]}), encoding="utf-8")
            result = apply_reminders(cfg, runtime, live=True)
            self.assertEqual(result["results"][0]["status"], "skipped-automatic-mode-off")

    def test_live_apply_skips_past_due_actions(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, runtime = make_env(Path(d))
            due = (dt.datetime.now(ZoneInfo("Africa/Cairo")) - dt.timedelta(minutes=10)).isoformat()
            action = {"action_id": "past-a", "operation": "create", "title": "past reminder", "due": due, "all_day": False, "list": "Personal Brain", "notes": "", "source_id": "s", "source_path": "p", "confidence": "high", "approval": "manual", "status": "planned"}
            (cfg.bridge / "outbox/reminders/actions.json").write_text(json.dumps({"actions": [action]}), encoding="utf-8")
            result = apply_reminders(cfg, runtime, live=True)
            self.assertEqual(result["results"][0]["status"], "skipped-past-due")
            actions = json.loads((cfg.bridge / "outbox/reminders/actions.json").read_text(encoding="utf-8"))["actions"]
            self.assertEqual(actions[0]["status"], "skipped")

    def test_live_apply_marks_mapped_actions_applied(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, runtime = make_env(Path(d))
            due = (dt.datetime.now(ZoneInfo("Africa/Cairo")) + dt.timedelta(minutes=10)).isoformat()
            action = {"action_id": "mapped-a", "operation": "create", "title": "mapped reminder", "due": due, "all_day": False, "list": "Personal Brain", "notes": "", "source_id": "s", "source_path": "p", "confidence": "high", "approval": "manual", "status": "planned"}
            (cfg.bridge / "outbox/reminders/actions.json").write_text(json.dumps({"actions": [action]}), encoding="utf-8")
            (cfg.engine / "data/state/apple-reminder-mappings.json").write_text(json.dumps({"version": 1, "actions": {"mapped-a": {"apple_id": "x", "title": "mapped reminder", "due": due}}}), encoding="utf-8")
            result = apply_reminders(cfg, runtime, live=True)
            self.assertEqual(result["results"][0]["status"], "skipped-duplicate")
            actions = json.loads((cfg.bridge / "outbox/reminders/actions.json").read_text(encoding="utf-8"))["actions"]
            self.assertEqual(actions[0]["status"], "applied")

    def test_apply_ignores_non_planned_unmapped_actions(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, runtime = make_env(Path(d))
            due = (dt.datetime.now(ZoneInfo("Africa/Cairo")) + dt.timedelta(minutes=10)).isoformat()
            action = {"action_id": "skipped-a", "operation": "create", "title": "skipped reminder", "due": due, "all_day": False, "list": "Personal Brain", "notes": "", "source_id": "s", "source_path": "p", "confidence": "high", "approval": "manual", "status": "skipped"}
            (cfg.bridge / "outbox/reminders/actions.json").write_text(json.dumps({"actions": [action]}), encoding="utf-8")
            result = apply_reminders(cfg, runtime, live=False)
            self.assertEqual(result["results"][0]["status"], "skipped-action-status")

    def test_live_required_review_action_fails_validation(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, runtime = make_env(Path(d))
            action = {"action_id": "a", "operation": "create", "title": "x", "due": None, "all_day": False, "list": "Personal Brain", "notes": "", "source_id": "s", "source_path": "p", "confidence": "low", "approval": "required-review", "status": "planned"}
            (cfg.bridge / "outbox/reminders/actions.json").write_text(json.dumps({"actions": [action, action]}), encoding="utf-8")
            self.assertTrue(any("duplicate reminder" in e for e in validate_all(cfg)))

    def test_lock_blocks_second_run(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "run.lock"
            with BrainLock(path):
                with self.assertRaises(RuntimeError):
                    with BrainLock(path):
                        pass

    def test_lock_recovers_when_recorded_pid_is_dead(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "run.lock"
            path.write_text("pid=999999\ncreated_at=2026-07-19T00:00:00+00:00\n", encoding="utf-8")
            with BrainLock(path):
                self.assertIn(f"pid={os.getpid()}", path.read_text(encoding="utf-8"))

    def test_runner_files_use_absolute_paths(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, runtime = make_env(Path(d))
            wrapper = write_wrapper(cfg, runtime)
            plist = write_plist(cfg, runtime)
            self.assertIn(str(cfg.engine), wrapper.read_text(encoding="utf-8"))
            self.assertIn(str(wrapper), plist.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
