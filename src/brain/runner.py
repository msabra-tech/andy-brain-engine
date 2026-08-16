from __future__ import annotations

import datetime as dt
import os
import plistlib
import subprocess
from pathlib import Path
from .codex_integration import codex_auth_check
from .config import Config, RuntimeConfig
from .state import read_json, write_json


DEFAULT_LABEL = "com.personalbrain.inbox-runner"


def runner_label(runtime: RuntimeConfig | None = None) -> str:
    return (runtime.runner_label if runtime else DEFAULT_LABEL) or DEFAULT_LABEL


def wrapper_path(config: Config, runtime: RuntimeConfig | None = None) -> Path:
    name = (runtime.runner_wrapper_name if runtime else "personal-brain-inbox-runner") or "personal-brain-inbox-runner"
    return config.engine / "scripts/runner" / name


def plist_path(config: Config, runtime: RuntimeConfig | None = None) -> Path:
    return config.engine / "launchd" / f"{runner_label(runtime)}.plist"


def installed_plist_path(runtime: RuntimeConfig | None = None) -> Path:
    return Path.home() / "Library/LaunchAgents" / f"{runner_label(runtime)}.plist"


def write_wrapper(config: Config, runtime: RuntimeConfig) -> Path:
    wrapper = wrapper_path(config, runtime)
    path_parts = [
        str(Path(runtime.python3_executable).parent) if runtime.python3_executable else "/usr/bin",
        str(Path(runtime.codex_executable).parent) if runtime.codex_executable else "/Applications/Codex.app/Contents/Resources",
        str(Path(runtime.shortcuts_executable).parent) if runtime.shortcuts_executable else "/usr/bin",
        str(Path(runtime.osascript_executable).parent) if runtime.osascript_executable else "/usr/bin",
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ]
    safe_path = ":".join(dict.fromkeys(path_parts))
    wrapper.write_text(f"""#!/bin/sh
set -eu
export PATH='{safe_path}'
cd '{config.engine}'
exec '{runtime.python3_executable}' '{config.engine / "brain"}' run-once
""", encoding="utf-8")
    wrapper.chmod(0o755)
    return wrapper


def write_plist(config: Config, runtime: RuntimeConfig) -> Path:
    wrapper = write_wrapper(config, runtime)
    logs = config.engine / "data/logs/runner"
    logs.mkdir(parents=True, exist_ok=True)
    payload = {
        "Label": runner_label(runtime),
        "ProgramArguments": [str(wrapper)],
        "StartInterval": int(runtime.scan_interval_seconds),
        "RunAtLoad": True,
        "StandardOutPath": str(logs / "stdout.log"),
        "StandardErrorPath": str(logs / "stderr.log"),
        "EnvironmentVariables": {
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/Applications/Codex.app/Contents/Resources"
        },
    }
    path = plist_path(config, runtime)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps(payload))
    return path


def preflight(config: Config, runtime: RuntimeConfig) -> dict[str, object]:
    tests = subprocess.run([runtime.python3_executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"], cwd=config.engine, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False, timeout=120)
    verify = subprocess.run([str(config.engine / "brain"), "verify"], cwd=config.engine, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False, timeout=120)
    codex = codex_auth_check(config, runtime)
    ok = tests.returncode == 0 and verify.returncode == 0 and bool(codex.get("ok"))
    return {"ok": ok, "tests": tests.returncode, "verify": verify.returncode, "codex": codex}


def install(config: Config, runtime: RuntimeConfig, skip_preflight: bool = False) -> dict[str, object]:
    check = {"ok": True, "skipped": True} if skip_preflight else preflight(config, runtime)
    if not check.get("ok"):
        return {"installed": False, "preflight": check}
    src = write_plist(config, runtime)
    target = installed_plist_path(runtime)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(src.read_bytes())
    bootout(runtime)
    proc = subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(target)], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if proc.returncode == 0:
        subprocess.run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{runner_label(runtime)}"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    result = {"installed": target.exists(), "loaded": proc.returncode == 0, "bootstrap_status": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr, "preflight": check}
    _write_status(config, result)
    return result


def bootout(runtime: RuntimeConfig | None = None) -> dict[str, object]:
    proc = subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{runner_label(runtime)}"], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    return {"status": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}


def start(config: Config, runtime: RuntimeConfig) -> dict[str, object]:
    target = installed_plist_path(runtime)
    label = runner_label(runtime)
    before = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{label}"], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    bootstrap_status = None
    bootstrap_stderr = ""
    if before.returncode != 0 and target.exists():
        bootstrap = subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(target)], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        bootstrap_status = bootstrap.returncode
        bootstrap_stderr = bootstrap.stderr
    proc = subprocess.run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{label}"], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    status = runner_status(config, runtime)
    status["bootstrap_status"] = bootstrap_status
    status["bootstrap_stderr"] = bootstrap_stderr
    status["start_status"] = proc.returncode
    status["start_stderr"] = proc.stderr
    _write_status(config, status)
    return status


def stop(config: Config, runtime: RuntimeConfig) -> dict[str, object]:
    result = bootout(runtime)
    status = runner_status(config, runtime)
    status["stop_status"] = result["status"]
    _write_status(config, status)
    return status


def uninstall(config: Config, runtime: RuntimeConfig) -> dict[str, object]:
    result = bootout(runtime)
    target = installed_plist_path(runtime)
    if target.exists():
        target.unlink()
    status = runner_status(config, runtime)
    status["uninstall_bootout_status"] = result["status"]
    _write_status(config, status)
    return status


def runner_status(config: Config, runtime: RuntimeConfig | None = None) -> dict[str, object]:
    label = runner_label(runtime)
    target = installed_plist_path(runtime)
    proc = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{label}"], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    payload = {
        "installed": target.exists(),
        "loaded": proc.returncode == 0,
        "label": label,
        "plist": str(target),
        "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "last_stdout_log": str(config.engine / "data/logs/runner/stdout.log"),
        "last_stderr_log": str(config.engine / "data/logs/runner/stderr.log"),
    }
    _write_status(config, payload)
    return payload


def logs(config: Config) -> dict[str, object]:
    out = config.engine / "data/logs/runner/stdout.log"
    err = config.engine / "data/logs/runner/stderr.log"
    return {
        "stdout_tail": out.read_text(encoding="utf-8")[-4000:] if out.exists() else "",
        "stderr_tail": err.read_text(encoding="utf-8")[-4000:] if err.exists() else "",
    }


def run_now(config: Config, runtime: RuntimeConfig) -> dict[str, object]:
    proc = subprocess.run([runtime.python3_executable, str(config.engine / "brain"), "run-once"], cwd=config.engine, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False, timeout=300)
    payload = {"exit_status": proc.returncode, "output": proc.stdout, "at": dt.datetime.now(dt.timezone.utc).isoformat()}
    write_json(config.engine / "data/state/runner-run-now.json", payload)
    return payload


def _write_status(config: Config, payload: dict[str, object]) -> None:
    write_json(config.engine / "data/state/runner-status.json", payload)
