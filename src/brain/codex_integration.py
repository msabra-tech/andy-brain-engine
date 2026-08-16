from __future__ import annotations

import datetime as dt
import json
import subprocess
from pathlib import Path
from .config import Config, RuntimeConfig
from .state import write_json


def codex_base_command(config: Config, runtime: RuntimeConfig) -> list[str]:
    return [
        runtime.codex_executable,
        "exec",
        "--cd",
        str(config.engine),
        "--add-dir",
        str(config.vault),
        "--add-dir",
        str(config.bridge),
        "--sandbox",
        "workspace-write",
        "--skip-git-repo-check",
        "--ephemeral",
        "--json",
        "-c",
        'approval_policy="never"',
    ]


def codex_auth_check(config: Config, runtime: RuntimeConfig, timeout: int = 90) -> dict[str, object]:
    if not runtime.codex_enabled:
        return {"ok": True, "skipped": True, "reason": "codex disabled in runtime config"}
    if not runtime.codex_executable or not Path(runtime.codex_executable).exists():
        return {"ok": False, "error": f"codex executable not found: {runtime.codex_executable}"}
    started = dt.datetime.now(dt.timezone.utc)
    proc = subprocess.run(
        codex_base_command(config, runtime) + ["Reply exactly: CODEX_AUTH_OK"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=timeout,
    )
    finished = dt.datetime.now(dt.timezone.utc)
    ok = proc.returncode == 0 and "CODEX_AUTH_OK" in proc.stdout
    return {
        "ok": ok,
        "exit_status": proc.returncode,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "stdout_tail": proc.stdout[-4000:],
        "stderr_tail": proc.stderr[-4000:],
    }


def invoke_codex_for_source(config: Config, runtime: RuntimeConfig, source_path: Path, source_id: str, source_hash: str, timeout: int = 180) -> dict[str, object]:
    if not runtime.codex_enabled:
        return {"ok": True, "skipped": True, "reason": "codex disabled in runtime config"}
    prompt = f"""You are running as the {runtime.vault_title} unattended ingestion reviewer.

Source id: {source_id}
Source hash: {source_hash}
Bridge source path: {source_path}

Read the source and classify atomic information as task, reminder, shopping, project update, idea, decision, reflection, person, question, or reference. Return a concise JSON summary only. Do not modify files; the deterministic engine will apply the safe V1 updates and preserve source boundaries."""
    started = dt.datetime.now(dt.timezone.utc)
    proc = subprocess.run(
        codex_base_command(config, runtime) + [prompt],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=timeout,
    )
    finished = dt.datetime.now(dt.timezone.utc)
    record = {
        "source_id": source_id,
        "source_hash": source_hash,
        "source_path": str(source_path),
        "exit_status": proc.returncode,
        "ok": proc.returncode == 0,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }
    log_path = config.engine / "data/logs/codex" / f"{source_hash[:16]}.json"
    write_json(log_path, record)
    return {k: v for k, v in record.items() if k not in {"stdout", "stderr"}}
