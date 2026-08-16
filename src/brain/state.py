from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    for attempt in range(3):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except OSError:
            if attempt == 2:
                raise
            time.sleep(1)
    return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, sort_keys=True) + "\n"
    last_error: OSError | None = None
    for attempt in range(5):
        tmp: Path | None = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
                handle.write(payload)
                tmp = Path(handle.name)
            os.replace(tmp, path)
            return
        except OSError as exc:
            last_error = exc
            if tmp:
                tmp.unlink(missing_ok=True)
            if attempt == 4:
                break
            time.sleep(1)
    raise last_error or OSError(f"could not write {path}")
