from __future__ import annotations

import re
from pathlib import Path

from .config import Config, is_relative_to


ALLOWED_TOP_LEVEL = {
    ".obsidian",
    "Home.md",
    "Welcome.md",
    "00 Command Center",
    "10 Active Work",
    "20 People",
    "30 Decisions and Insights",
    "40 Chat Handoffs",
    "90 Archive",
}
REQUIRED = [
    "Home.md",
    "Welcome.md",
    "00 Command Center/Home.md",
    "00 Command Center/Needs Attention.md",
    "10 Active Work",
    "20 People/Michael.md",
    "30 Decisions and Insights/Decisions.md",
    "40 Chat Handoffs/Current Context.md",
]
SECRET_RE = re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|secret|password|credential)\b\s*[:=]\s*[\"']?([^\"'\s#]+)")
WIKILINK_RE = re.compile(r"!?\[\[([^\]]+)\]\]")


def validate_all(config: Config) -> list[str]:
    errors: list[str] = []
    for label, path in {"engine": config.engine, "vault": config.vault, "staging": config.bridge}.items():
        if not path.exists():
            errors.append(f"{label} missing: {path}")
    if is_relative_to(config.bridge, config.vault):
        errors.append("temporary staging is inside the vault")
    if is_relative_to(config.engine, config.vault):
        errors.append("engine is inside the vault")
    if config.vault.exists():
        _validate_vault(config.vault, errors)
    _validate_no_raw_retention(config, errors)
    return errors


def _validate_vault(vault: Path, errors: list[str]) -> None:
    for child in vault.iterdir():
        if child.name not in ALLOWED_TOP_LEVEL:
            errors.append(f"unexpected top-level vault item: {child.name}")
    for relative in REQUIRED:
        if not (vault / relative).exists():
            errors.append(f"vault missing required artifact: {relative}")
    for path in vault.rglob("*.json"):
        if ".obsidian" not in path.parts:
            errors.append(f"runtime JSON exists in vault: {path.relative_to(vault).as_posix()}")
    check_links(vault, errors)
    check_secrets(vault, errors)


def _validate_no_raw_retention(config: Config, errors: list[str]) -> None:
    raw = config.engine / "data/raw"
    if raw.exists() and any(path.is_file() for path in raw.rglob("*")):
        errors.append("raw source retention exists in data/raw")
    archive = config.engine / "data/state/archive_manifest.json"
    if archive.exists():
        errors.append("raw archive manifest exists; Andy Brain must retain only source metadata")


def check_links(vault: Path, errors: list[str]) -> None:
    notes = {path.relative_to(vault).with_suffix("").as_posix() for path in vault.rglob("*.md")}
    basenames: dict[str, int] = {}
    for path in vault.rglob("*.md"):
        basenames[path.stem] = basenames.get(path.stem, 0) + 1
    for path in vault.rglob("*.md"):
        for match in WIKILINK_RE.finditer(path.read_text(encoding="utf-8")):
            target = match.group(1).split("|", 1)[0].split("#", 1)[0].strip().removesuffix(".md")
            if not target or target.startswith(("http://", "https://")):
                continue
            if target in notes or ("/" not in target and basenames.get(target) == 1):
                continue
            errors.append(f"{path.relative_to(vault).as_posix()} has broken wikilink: {target}")


def check_secrets(root: Path, errors: list[str]) -> None:
    for path in root.rglob("*.md"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            match = SECRET_RE.search(line)
            if match and not any(word in line.lower() for word in ["placeholder", "not-a-real", "do not", "never store"]):
                errors.append(f"secret-looking value in {path.relative_to(root).as_posix()}:{number}")
