from __future__ import annotations

import datetime as dt
from .config import Config, RuntimeConfig
from .reminders import detect_adapter
from .state import write_json


def detect(runtime: RuntimeConfig | None = None) -> dict[str, object]:
    if runtime is None:
        from .config import load_config, load_runtime
        config = load_config()
        runtime = load_runtime(config)
    return detect_adapter(runtime)


def export_context(config: Config, runtime: RuntimeConfig) -> dict[str, object]:
    payload = {
        "version": 1,
        "exported_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "adapter": detect_adapter(runtime),
        "reminders": [],
        "note": "Live Apple context export is fixture-safe until permissions are explicitly granted.",
    }
    write_json(config.bridge / "inbox/apple-context/apple-context.json", payload)
    return payload
