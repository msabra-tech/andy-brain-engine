from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Config:
    engine: Path
    vault: Path
    bridge: Path


@dataclass(frozen=True)
class RuntimeConfig:
    automatic_ingestion: bool = True
    automatic_publishing: bool = True
    automatic_reminders: bool = False
    owner_name: str = "Owner"
    vault_title: str = "Personal Brain"
    reminder_list: str = "Personal Brain"
    scan_interval_seconds: int = 60
    stable_wait_seconds: int = 5
    default_timezone: str = "UTC"
    codex_executable: str = ""
    python3_executable: str = ""
    shortcuts_executable: str = ""
    osascript_executable: str = ""
    codex_enabled: bool = True
    plaud_mcp_status: str = "not_configured"
    plaud_mcp_command: str = ""
    plaud_mcp_notes: str = ""
    runner_label: str = "com.personalbrain.inbox-runner"
    runner_wrapper_name: str = "personal-brain-inbox-runner"


def engine_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_config() -> Config:
    root = engine_root()
    path = root / "config" / "paths.local.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return Config(
        engine=Path(data["engine"]).expanduser().resolve(),
        vault=Path(data["vault"]).expanduser().resolve(),
        bridge=Path(data["bridge"]).expanduser().resolve(),
    )


def load_runtime(config: Config | None = None) -> RuntimeConfig:
    config = config or load_config()
    path = config.engine / "config" / "runtime.local.json"
    if not path.exists():
        data: dict[str, Any] = {}
    else:
        data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("codex_executable", shutil.which("codex") or "/Applications/Codex.app/Contents/Resources/codex")
    data.setdefault("python3_executable", shutil.which("python3") or "/usr/bin/python3")
    data.setdefault("shortcuts_executable", shutil.which("shortcuts") or "/usr/bin/shortcuts")
    data.setdefault("osascript_executable", shutil.which("osascript") or "/usr/bin/osascript")
    return RuntimeConfig(**{k: v for k, v in data.items() if k in RuntimeConfig.__dataclass_fields__})


def save_runtime(config: Config, runtime: RuntimeConfig) -> None:
    path = config.engine / "config" / "runtime.local.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(runtime.__dict__, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def set_runtime_value(config: Config, key: str, value: object) -> RuntimeConfig:
    runtime = load_runtime(config)
    normalized = key.replace("-", "_")
    if normalized not in RuntimeConfig.__dataclass_fields__:
        raise KeyError(f"unknown runtime config key: {key}")
    data = dict(runtime.__dict__)
    data[normalized] = value
    updated = RuntimeConfig(**data)
    save_runtime(config, updated)
    return updated


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False
