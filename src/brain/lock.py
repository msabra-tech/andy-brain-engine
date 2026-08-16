from __future__ import annotations

import datetime as dt
import os
from pathlib import Path


class BrainLockActive(RuntimeError):
    pass


class BrainLock:
    def __init__(self, path: Path, stale_seconds: int = 600):
        self.path = path
        self.stale_seconds = stale_seconds
        self.acquired = False

    def __enter__(self) -> "BrainLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        now = dt.datetime.now(dt.timezone.utc)
        if self.path.exists():
            age = now.timestamp() - self.path.stat().st_mtime
            pid = _lock_pid(self.path)
            if (pid is not None and not _pid_is_running(pid)) or age > self.stale_seconds:
                self.path.unlink(missing_ok=True)
            else:
                raise BrainLockActive(f"another brain run is active: {self.path}")
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        fd = os.open(self.path, flags)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(f"pid={os.getpid()}\ncreated_at={now.isoformat()}\n")
        self.acquired = True
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.acquired:
            self.path.unlink(missing_ok=True)


def _lock_pid(path: Path) -> int | None:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("pid="):
                return int(line.split("=", 1)[1])
    except (OSError, ValueError):
        return None
    return None


def _pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
