from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import replace
from .apple import export_context
from .codex_integration import codex_auth_check
from .config import load_config, load_runtime, save_runtime, set_runtime_value
from .fixtures import create_dynamic_fixture
from .ingest import ingest
from .lock import BrainLock, BrainLockActive
from .publish import publish
from .reminders import apply_reminders, doctor, plan_test_reminder, retry_failed, rollback_test, status as reminder_status, validate_actions
from .runner import install as runner_install, logs as runner_logs, run_now as runner_run_now, runner_status, start as runner_start, stop as runner_stop, uninstall as runner_uninstall
from .state import write_json
from .status import build_status
from .validation import validate_all


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="brain")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("audit")
    sub.add_parser("ingest")
    sub.add_parser("publish")
    sub.add_parser("review")
    sub.add_parser("verify")
    sub.add_parser("run-once")
    sub.add_parser("status")
    config_cmd = sub.add_parser("config")
    config_sub = config_cmd.add_subparsers(dest="config_command", required=True)
    config_set = config_sub.add_parser("set")
    config_set.add_argument("key")
    config_set.add_argument("value")
    apple = sub.add_parser("apple")
    apple_sub = apple.add_subparsers(dest="apple_command", required=True)
    apple_sub.add_parser("export")
    reminders = sub.add_parser("reminders")
    rem_sub = reminders.add_subparsers(dest="reminders_command", required=True)
    rem_sub.add_parser("doctor")
    rem_sub.add_parser("plan")
    apply = rem_sub.add_parser("apply")
    mode = apply.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--live", action="store_true")
    rem_sub.add_parser("status")
    rem_sub.add_parser("retry")
    rem_sub.add_parser("rollback-test")
    runner = sub.add_parser("runner")
    runner_sub = runner.add_subparsers(dest="runner_command", required=True)
    runner_sub.add_parser("install")
    runner_sub.add_parser("start")
    runner_sub.add_parser("stop")
    runner_sub.add_parser("restart")
    runner_sub.add_parser("status")
    runner_sub.add_parser("logs")
    runner_sub.add_parser("run-now")
    runner_sub.add_parser("uninstall")
    schedule = sub.add_parser("schedule")
    sched_sub = schedule.add_subparsers(dest="schedule_command", required=True)
    sched_sub.add_parser("install")
    sched_sub.add_parser("status")
    sched_sub.add_parser("uninstall")
    fixture = sub.add_parser("fixture")
    fixture_sub = fixture.add_subparsers(dest="fixture_command", required=True)
    fixture_create = fixture_sub.add_parser("create")
    fixture_create.add_argument("--name", default="dynamic-demo")
    args = parser.parse_args(argv)
    config = load_config()
    runtime = load_runtime(config)

    if args.command == "audit":
        return _print(_audit(config, runtime))
    if args.command == "ingest":
        return _print(ingest(config, runtime))
    if args.command == "publish":
        publish(config, runtime)
        return _print({"published": True})
    if args.command == "review":
        errors = validate_all(config)
        return _print({"findings": errors}, 1 if errors else 0)
    if args.command == "verify":
        errors = validate_all(config)
        if errors:
            print(f"{runtime.vault_title} verification failed:")
            for error in errors:
                print(f"ERROR: {error}")
            return 1
        print(f"{runtime.vault_title} verification passed")
        return 0
    if args.command == "run-once":
        return _run_once(config, runtime)
    if args.command == "status":
        return _print(build_status(config, runtime))
    if args.command == "config":
        return _config_set(config, runtime, args.key, args.value)
    if args.command == "apple" and args.apple_command == "export":
        return _print(export_context(config, runtime))
    if args.command == "reminders":
        return _reminders(config, runtime, args)
    if args.command == "runner":
        return _runner(config, runtime, args.runner_command)
    if args.command == "schedule":
        print("Schedule commands are superseded by runner commands; use ./brain runner install/status/uninstall.")
        return 0 if args.schedule_command == "status" else 1
    if args.command == "fixture" and args.fixture_command == "create":
        path = create_dynamic_fixture(config, runtime, args.name)
        return _print({"created": str(path)})
    return 2


def _run_once(config, runtime) -> int:
    try:
        lock = BrainLock(config.engine / "data/locks/run-once.lock")
        lock.__enter__()
    except BrainLockActive as exc:
        payload = {"ok": False, "skipped_due_lock": True, "error": str(exc)}
        write_json(config.engine / "data/state/last-run.json", payload)
        print(f"{runtime.vault_title} run skipped: {exc}")
        return 75
    try:
        counts = ingest(config, runtime, invoke_codex=runtime.codex_enabled, wait_for_stable=True)
        publish(config, runtime)
        action_errors = validate_actions(config)
        if action_errors:
            write_json(config.engine / "data/state/last-run.json", {"ok": False, "errors": action_errors, "counts": counts})
            print("run-once failed action validation:")
            for error in action_errors:
                print(f"ERROR: {error}")
            return 1
        reminder_result = {"skipped": True, "automatic_reminders": False}
        if runtime.automatic_reminders:
            reminder_result = apply_reminders(config, runtime, live=True, automatic_only=True)
        errors = validate_all(config)
        ok = not errors
        write_json(config.engine / "data/state/last-run.json", {"ok": ok, "counts": counts, "reminders": reminder_result, "errors": errors})
        if errors:
            print(f"{runtime.vault_title} verification failed:")
            for error in errors:
                print(f"ERROR: {error}")
            return 1
        print(f"{runtime.vault_title} verification passed")
        return 0
    finally:
        lock.__exit__(None, None, None)


def _audit(config, runtime):
    from .reminders import detect_adapter
    payload = {
        "engine": str(config.engine),
        "vault": str(config.vault),
        "bridge": str(config.bridge),
        "codex": codex_auth_check(config, runtime),
        "reminders": detect_adapter(runtime),
        "vault_top_level": sorted(p.name for p in config.vault.iterdir()),
        "bridge_top_level": sorted(p.name for p in config.bridge.iterdir()),
    }
    write_json(config.engine / "docs/AUDIT_REPORT.json", payload)
    return payload


def _config_set(config, runtime, key: str, value: str) -> int:
    normalized = key.replace("-", "_")
    if normalized == "automatic_reminders":
        wants_true = value.lower() == "true"
        if wants_true:
            doc = doctor(config, runtime)
            dry = apply_reminders(config, runtime, live=False)
            mappings = __import__("json").loads((config.engine / "data/state/apple-reminder-mappings.json").read_text(encoding="utf-8")) if (config.engine / "data/state/apple-reminder-mappings.json").exists() else {"actions": {}}
            if not doc.get("ok") or not dry.get("results") or not mappings.get("actions"):
                return _print({"enabled": False, "reason": "doctor, dry-run, and one successful live mapped reminder are required before enabling automatic reminders"}, 1)
        updated = set_runtime_value(config, normalized, wants_true)
        return _print({"automatic_reminders": updated.automatic_reminders})
    if value.lower() in {"true", "false"}:
        parsed: object = value.lower() == "true"
    else:
        try:
            parsed = int(value)
        except ValueError:
            parsed = value
    updated = set_runtime_value(config, normalized, parsed)
    return _print({normalized: getattr(updated, normalized)})


def _reminders(config, runtime, args) -> int:
    if args.reminders_command == "doctor":
        result = doctor(config, runtime)
        return _print(result, 0 if result.get("ok") else 1)
    if args.reminders_command == "plan":
        return _print(plan_test_reminder(config, runtime))
    if args.reminders_command == "apply":
        return _print(apply_reminders(config, runtime, live=args.live))
    if args.reminders_command == "status":
        return _print(reminder_status(config))
    if args.reminders_command == "retry":
        return _print(retry_failed(config))
    if args.reminders_command == "rollback-test":
        return _print(rollback_test(config))
    return 2


def _runner(config, runtime, command: str) -> int:
    if command == "install":
        result = runner_install(config, runtime)
        return _print(result, 0 if result.get("installed") else 1)
    if command == "start":
        return _print(runner_start(config, runtime))
    if command == "stop":
        return _print(runner_stop(config, runtime))
    if command == "restart":
        runner_stop(config, runtime)
        return _print(runner_start(config, runtime))
    if command == "status":
        return _print(runner_status(config, runtime))
    if command == "logs":
        return _print(runner_logs(config))
    if command == "run-now":
        result = runner_run_now(config, runtime)
        return _print(result, 0 if result.get("exit_status") == 0 else 1)
    if command == "uninstall":
        return _print(runner_uninstall(config, runtime))
    return 2


def _print(payload, exit_code: int = 0) -> int:
    print(json.dumps(payload, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
