from __future__ import annotations

import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo
from .config import Config, RuntimeConfig


def create_dynamic_fixture(config: Config, runtime: RuntimeConfig, name: str = "dynamic-demo") -> Path:
    now = dt.datetime.now(ZoneInfo(runtime.default_timezone)).replace(microsecond=0)
    future = now + dt.timedelta(minutes=10)
    path = config.bridge / "inbox/mobile" / f"{name}-{now.strftime('%Y%m%d-%H%M%S')}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"""---
captured_at: {now.isoformat()}
timezone: {runtime.default_timezone}
source_type: demo-fixture
source_title: Dynamic unattended loop demo
speaker: {runtime.owner_name}
---

DEMO FIXTURE - NOT HAGAR'S REAL DATA

Shopping list: add shampoo and bottled water.
To-do: compare two restaurants for tomorrow.
Remind me at {future.isoformat()} to check the {runtime.vault_title} notification.
I have an idea to let voice-note transcripts automatically update the same shopping list instead of creating duplicates.
I already bought the demonstration toothpaste.
""", encoding="utf-8")
    return path
