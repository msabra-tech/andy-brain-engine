from __future__ import annotations

import json
import re
from pathlib import Path
from .config import Config, is_relative_to, load_runtime
from .hashing import sha256_file
from .reminders import validate_actions


DEV_TOP_LEVEL = {
    ".agents", ".git", "AGENTS.md", "prompts", "scripts", "tests",
    "brain", ".gitignore", "Connectors", "Automation", "Attachments",
    "Links", "Transcripts", "Inbox", "README.md", "Untitled.base",
    "Demo", "System", "Templates", "Sources", "Review",
}
ALLOWED_TOP_LEVEL = {".obsidian", "Home.md", "Ideas", "Life", "People", "Projects", "Work", "Career", "References", "Quick Capture.md", "Today.md", "Welcome.md"}
SECRET_RE = re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|secret|password|credential)\b\s*[:=]\s*[\"']?([^\"'\s#]+)")
WIKILINK_RE = re.compile(r"!?\[\[([^\]]+)\]\]")


def validate_all(config: Config) -> list[str]:
    errors: list[str] = []
    validate_paths(config, errors)
    validate_vault(config.vault, errors)
    validate_bridge(config, errors)
    validate_raw_archives(config.engine, errors)
    return errors


def validate_paths(config: Config, errors: list[str]) -> None:
    if is_relative_to(config.bridge, config.vault):
        errors.append("bridge is inside the vault")
    if "Mobile Documents" in config.engine.as_posix() or "CloudDocs" in config.engine.as_posix():
        errors.append("engine is inside iCloud")
    for label, path in {"engine": config.engine, "vault": config.vault, "bridge": config.bridge}.items():
        if not path.exists():
            errors.append(f"{label} missing: {path}")


def validate_vault(vault: Path, errors: list[str]) -> None:
    for child in vault.iterdir():
        if child.name in DEV_TOP_LEVEL:
            errors.append(f"developer or runtime file exists in vault: {child.name}")
        if child.name not in ALLOWED_TOP_LEVEL:
            errors.append(f"unexpected top-level vault item: {child.name}")
    for required in ["Home.md", "Today.md", "Welcome.md", "Quick Capture.md", "References/Source Notes.md", "References/Open Questions.md"]:
        if not (vault / required).exists():
            errors.append(f"vault missing required file: {required}")
    for path in vault.rglob("*.json"):
        if ".obsidian" not in path.parts:
            errors.append(f"raw state JSON exists in vault: {path.relative_to(vault).as_posix()}")
    check_links(vault, errors)
    check_sources(vault, errors)
    check_secrets(vault, errors)


def check_links(vault: Path, errors: list[str]) -> None:
    notes = {p.relative_to(vault).with_suffix("").as_posix(): p for p in vault.rglob("*.md")}
    basenames: dict[str, list[Path]] = {}
    for p in vault.rglob("*.md"):
        basenames.setdefault(p.stem, []).append(p)
    for path in vault.rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        for match in WIKILINK_RE.finditer(text):
            target = match.group(1).split("|", 1)[0].split("#", 1)[0].strip()
            if not target or target.startswith(("http://", "https://")):
                continue
            if target.endswith(".md"):
                target = target[:-3]
            if target in notes:
                continue
            if "/" not in target and len(basenames.get(target, [])) == 1:
                continue
            errors.append(f"{path.relative_to(vault).as_posix()} has broken wikilink: {target}")


def check_sources(vault: Path, errors: list[str]) -> None:
    if not (vault / "References/Sources").exists():
        errors.append("source cards directory missing: References/Sources")
    for folder in ["Projects", "Ideas", "Life", "Work", "Career", "People"]:
        root = vault / folder
        if not root.exists():
            continue
        for path in root.rglob("*.md"):
            text = path.read_text(encoding="utf-8")
            if " Index.md" in path.name:
                continue
            if "Source:" not in text and "source_refs:" not in text and "Source boundary:" not in text:
                errors.append(f"canonical note lacks required source information: {path.relative_to(vault).as_posix()}")


def check_secrets(root: Path, errors: list[str]) -> None:
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in {".md", ".txt", ".json"}:
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for line_no, line in enumerate(lines, 1):
            match = SECRET_RE.search(line)
            if match and not any(word in line.lower() for word in ["placeholder", "not-a-real", "do not", "never store"]):
                errors.append(f"secret-looking value in {path.relative_to(root).as_posix()}:{line_no}")


def validate_bridge(config: Config, errors: list[str]) -> None:
    bridge = config.bridge
    for required in ["inbox/mobile", "inbox/audio", "inbox/text", "inbox/files", "outbox/reminders", "receipts/ingestion", "receipts/reminders", "errors"]:
        if not (bridge / required).exists():
            errors.append(f"bridge missing required directory: {required}")
    errors.extend(validate_actions(config))


def validate_raw_archives(engine: Path, errors: list[str]) -> None:
    manifest = engine / "data/state/archive_manifest.json"
    if manifest.exists():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        seen: set[str] = set()
        for item in data.get("items", []):
            digest = item["sha256"]
            if digest in seen:
                errors.append(f"duplicate source hash registered: {digest}")
            seen.add(digest)
            path = engine / item["path"]
            if not path.exists():
                errors.append(f"raw archive missing: {item['path']}")
            elif sha256_file(path) != digest:
                errors.append(f"raw archive modified: {item['path']}")
