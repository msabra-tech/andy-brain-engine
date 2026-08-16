from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo


FRONTMATTER_RE = re.compile(r"\A---\s*\n(?P<body>.*?)\n---+\s*\n", re.S)


@dataclass(frozen=True)
class SourceMetadata:
    captured_at: str
    timestamp_basis: str
    temporal_confidence: str
    timezone: str
    source_type: str
    source_title: str
    speaker: str | None


def parse_capture(path: Path, text: str, default_timezone: str) -> tuple[SourceMetadata, str]:
    data: dict[str, str] = {}
    body = text
    match = FRONTMATTER_RE.match(text)
    if match:
        body = text[match.end():]
        for line in match.group("body").splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                data[key.strip()] = value.strip().strip('"').strip("'")
    elif path.suffix == ".json":
        try:
            payload = json.loads(text)
            if isinstance(payload, dict):
                data.update({str(k): str(v) for k, v in payload.items() if k in {"captured_at", "timezone", "source_type", "source_title", "speaker"}})
                body = str(payload.get("text") or payload.get("content") or text)
        except json.JSONDecodeError:
            pass
    tz = data.get("timezone") or default_timezone
    captured = data.get("captured_at")
    if captured:
        captured_at = _normalize_datetime(captured, tz)
        basis = "supplied"
        confidence = "high"
    else:
        stat = path.stat()
        captured_at = dt.datetime.fromtimestamp(stat.st_mtime, ZoneInfo(tz)).isoformat()
        basis = "filesystem-arrival"
        confidence = "low"
    title = data.get("source_title") or path.stem.replace("-", " ").replace("_", " ").strip().title()
    metadata = SourceMetadata(
        captured_at=captured_at,
        timestamp_basis=basis,
        temporal_confidence=confidence,
        timezone=tz,
        source_type=data.get("source_type") or infer_source_type(path, body),
        source_title=title,
        speaker=data.get("speaker"),
    )
    return metadata, body


def _normalize_datetime(value: str, tz: str) -> str:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(tz))
    return parsed.isoformat()


def infer_source_type(path: Path, text: str) -> str:
    lower = text.lower()
    if "http://" in lower or "https://" in lower:
        return "url-text"
    if "transcript" in lower or "plaud" in lower:
        return "transcript text export"
    if "demo fixture" in lower:
        return "demo fixture"
    return "text capture"
