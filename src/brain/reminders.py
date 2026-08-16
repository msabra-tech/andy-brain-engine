from __future__ import annotations

import datetime as dt
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from zoneinfo import ZoneInfo
from .config import Config, RuntimeConfig
from .state import read_json, write_json


ACTIONS = "outbox/reminders/actions.json"


def stable_action_id(operation: str, title: str, due: str | None, source_id: str) -> str:
    raw = "|".join([operation, title.strip().lower(), due or "", source_id])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def load_actions(config: Config) -> dict:
    payload = read_json(config.bridge / ACTIONS, {"version": 1, "generated_at": None, "actions": []})
    payload["actions"] = [_normalize_action(action) for action in payload.get("actions", [])]
    return payload


def _normalize_action(action: dict) -> dict:
    normalized = dict(action)
    if "due" not in normalized and "due_date" in normalized:
        normalized["due"] = normalized.pop("due_date")
    if "approval" not in normalized and "approval_status" in normalized:
        legacy = normalized.pop("approval_status")
        normalized["approval"] = "manual" if legacy in {"approved", "approved_for_live_test"} else "required-review"
    normalized.setdefault("all_day", False)
    normalized.setdefault("source_path", normalized.get("source_id", "unknown"))
    normalized.setdefault("confidence", "high" if isinstance(normalized.get("confidence"), (float, int)) and normalized.get("confidence", 0) >= 0.8 else normalized.get("confidence", "high"))
    if normalized["confidence"] == 1.0:
        normalized["confidence"] = "high"
    normalized.setdefault("status", "planned")
    return normalized


def save_actions(config: Config, actions: list[dict]) -> dict:
    payload = {"version": 1, "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(), "actions": actions}
    write_json(config.bridge / ACTIONS, payload)
    return payload


def upsert_actions(config: Config, new_actions: list[dict]) -> dict:
    payload = load_actions(config)
    by_id = {action["action_id"]: action for action in payload.get("actions", [])}
    for action in new_actions:
        by_id.setdefault(action["action_id"], action)
    return save_actions(config, sorted(by_id.values(), key=lambda item: item["action_id"]))


def validate_actions(config: Config) -> list[str]:
    errors: list[str] = []
    payload = load_actions(config)
    seen: set[str] = set()
    for action in payload.get("actions", []):
        action_id = action.get("action_id")
        if not action_id:
            errors.append("reminder action missing action_id")
        elif action_id in seen:
            errors.append(f"duplicate reminder action id: {action_id}")
        seen.add(action_id)
        if action.get("operation") not in {"create", "complete"}:
            errors.append(f"unsupported reminder operation: {action.get('operation')}")
        if not action.get("title"):
            errors.append(f"reminder action missing title: {action_id}")
        if "due" not in action:
            errors.append(f"reminder action missing due: {action_id}")
        if action.get("approval") not in {"automatic", "manual", "required-review"}:
            errors.append(f"reminder action has invalid approval: {action_id}")
        if action.get("status") not in {"planned", "applied", "failed", "skipped", "review"}:
            errors.append(f"reminder action has invalid status: {action_id}")
    return errors


def detect_adapter(runtime: RuntimeConfig) -> dict[str, object]:
    shortcuts_path = runtime.shortcuts_executable if runtime.shortcuts_executable and Path(runtime.shortcuts_executable).exists() else shutil.which("shortcuts")
    osascript_path = runtime.osascript_executable if runtime.osascript_executable and Path(runtime.osascript_executable).exists() else shutil.which("osascript")
    shortcuts_list: list[str] = []
    if shortcuts_path:
        proc = subprocess.run([shortcuts_path, "list"], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=20)
        if proc.returncode == 0:
            shortcuts_list = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    live_access = False
    live_error = None
    if osascript_path:
        proc = subprocess.run([osascript_path, "-e", 'tell application "Reminders" to return count of lists'], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=20)
        live_access = proc.returncode == 0
        if proc.returncode != 0:
            live_error = (proc.stderr or proc.stdout).strip()
    return {
        "shortcuts": bool(shortcuts_path),
        "shortcuts_path": shortcuts_path,
        "shortcuts_list": shortcuts_list,
        "osascript": bool(osascript_path),
        "osascript_path": osascript_path,
        "reminders_live_access": live_access,
        "reminders_live_error": live_error,
        "selected_adapter": "applescript" if osascript_path else ("shortcuts" if shortcuts_path else "queue-only"),
    }


def doctor(config: Config, runtime: RuntimeConfig) -> dict[str, object]:
    adapter = detect_adapter(runtime)
    payload = {
        "ok": bool(adapter["osascript"] or adapter["shortcuts"]),
        "adapter": adapter,
        "dry_run_supported": True,
        "live_supported": bool(adapter["osascript"]),
        "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    write_json(config.engine / "data/state/reminder-doctor.json", payload)
    return payload


def plan_test_reminder(config: Config, runtime: RuntimeConfig) -> dict:
    due = (dt.datetime.now(ZoneInfo(runtime.default_timezone)) + dt.timedelta(minutes=10)).replace(microsecond=0).isoformat()
    title = f"{runtime.vault_title} live reminder test"
    action = {
        "action_id": stable_action_id("create", title, due, "manual-live-test"),
        "operation": "create",
        "title": title,
        "due": due,
        "all_day": False,
        "list": runtime.reminder_list,
        "notes": "Explicit live test reminder. Create only with ./brain reminders apply --live.",
        "source_id": "manual-live-test",
        "source_path": "bridge/outbox/reminders/actions.json",
        "confidence": "high",
        "approval": "manual",
        "status": "planned",
    }
    return upsert_actions(config, [action])


def eligible_for_automatic(action: dict, mappings: dict) -> bool:
    if action.get("operation") != "create":
        return False
    if action.get("approval") != "automatic":
        return False
    if action.get("confidence") != "high":
        return False
    if not action.get("due"):
        return False
    if action.get("action_id") in mappings.get("actions", {}):
        return False
    try:
        due = dt.datetime.fromisoformat(str(action["due"]))
    except ValueError:
        return False
    if due.tzinfo is None:
        due = due.replace(tzinfo=dt.timezone.utc)
    return due > dt.datetime.now(due.tzinfo)


def apply_reminders(config: Config, runtime: RuntimeConfig, live: bool, automatic_only: bool = False) -> dict[str, object]:
    errors = validate_actions(config)
    if errors:
        raise SystemExit("invalid reminder actions: " + "; ".join(errors))
    payload = load_actions(config)
    mappings_path = config.engine / "data/state/apple-reminder-mappings.json"
    mappings = read_json(mappings_path, {"version": 1, "actions": {}})
    results = []
    actions = payload.get("actions", [])
    changed_actions = False
    for action in actions:
        action_id = action["action_id"]
        if action_id in mappings.get("actions", {}):
            if live and action.get("status") != "applied":
                action["status"] = "applied"
                changed_actions = True
            receipt = _receipt(config, action, "skipped-duplicate", live)
            results.append(receipt)
            continue
        if action.get("status") != "planned":
            receipt = _receipt(config, action, "skipped-action-status", live, f"action status is {action.get('status')}")
            results.append(receipt)
            continue
        if automatic_only and not eligible_for_automatic(action, mappings):
            continue
        if live:
            if action.get("approval") not in {"automatic", "manual"}:
                action["status"] = "review"
                changed_actions = True
                receipt = _receipt(config, action, "failed-approval", live, "approval is required-review")
                results.append(receipt)
                continue
            future, due_error = _due_is_future(action.get("due"), runtime)
            if not future:
                action["status"] = "skipped"
                changed_actions = True
                receipt = _receipt(config, action, "skipped-past-due", live, due_error)
                results.append(receipt)
                continue
            if action.get("approval") == "automatic" and not (automatic_only or runtime.automatic_reminders):
                receipt = _receipt(config, action, "skipped-automatic-mode-off", live)
                results.append(receipt)
                continue
            try:
                apple_id = _create_live_reminder(action, runtime)
            except Exception as exc:  # AppleScript errors should become receipts.
                action["status"] = "failed"
                changed_actions = True
                receipt = _receipt(config, action, "failed", live, str(exc))
                results.append(receipt)
                continue
            mappings.setdefault("actions", {})[action_id] = {
                "apple_id": apple_id,
                "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "title": action["title"],
                "due": action.get("due"),
            }
            action["status"] = "applied"
            changed_actions = True
            status = "live-created"
        else:
            status = "dry-run"
        receipt = _receipt(config, action, status, live)
        results.append(receipt)
    write_json(mappings_path, mappings)
    if live and changed_actions:
        save_actions(config, actions)
    return {"version": 1, "results": results}


def _due_is_future(value: object, runtime: RuntimeConfig) -> tuple[bool, str | None]:
    if not value:
        return False, "live reminder requires a concrete due value"
    try:
        due = dt.datetime.fromisoformat(str(value))
    except ValueError:
        return False, "due value is not valid ISO-8601"
    if due.tzinfo is None:
        due = due.replace(tzinfo=ZoneInfo(runtime.default_timezone))
    now = dt.datetime.now(due.tzinfo)
    if due <= now:
        return False, "due value is not in the future"
    return True, None


def _receipt(config: Config, action: dict, status: str, live: bool, error: str | None = None) -> dict:
    receipt = {
        "version": 1,
        "action_id": action["action_id"],
        "status": status,
        "live": live,
        "title": action.get("title"),
        "due": action.get("due"),
        "at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    if error:
        receipt["error"] = error
    write_json(config.bridge / "receipts/reminders" / f"{action['action_id']}.json", receipt)
    return receipt


def _create_live_reminder(action: dict, runtime: RuntimeConfig) -> str:
    osascript = runtime.osascript_executable or shutil.which("osascript")
    if not osascript or not Path(osascript).exists():
        raise RuntimeError("osascript is unavailable; cannot create live Apple Reminder")
    due = action.get("due")
    if not due:
        raise RuntimeError("live reminder requires a concrete due value")
    script = f"""
    tell application "Reminders"
        if not (exists list "{_escape(str(action["list"]))}") then
            make new list with properties {{name:"{_escape(str(action["list"]))}"}}
        end if
        set targetList to list "{_escape(str(action["list"]))}"
        set newReminder to make new reminder at targetList with properties {{name:"{_escape(str(action["title"]))}", body:"{_escape(str(action.get("notes", "")))}"}}
        set due date of newReminder to date "{_apple_date(str(due))}"
        return id of newReminder
    end tell
    """
    proc = subprocess.run([osascript, "-e", script], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout).strip() or "Apple Reminder creation failed")
    return proc.stdout.strip()


def _apple_date(value: str) -> str:
    parsed = dt.datetime.fromisoformat(value)
    return parsed.strftime("%A, %B %d, %Y at %I:%M:%S %p")


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def status(config: Config) -> dict[str, object]:
    payload = load_actions(config)
    receipts = sorted((config.bridge / "receipts/reminders").glob("*.json"))
    mappings = read_json(config.engine / "data/state/apple-reminder-mappings.json", {"version": 1, "actions": {}})
    actions = payload.get("actions", [])
    return {
        "queue_count": sum(1 for action in actions if action.get("status") == "planned"),
        "total_action_count": len(actions),
        "mapped_count": len(mappings.get("actions", {})),
        "last_receipt": receipts[-1].name if receipts else None,
    }


def retry_failed(config: Config) -> dict[str, object]:
    return {"retried": 0, "note": "failed reminder receipts remain available; rerun apply after fixing permissions"}


def rollback_test(config: Config) -> dict[str, object]:
    return {"rolled_back": False, "note": "test reminder cleanup requires explicit user authorization"}
