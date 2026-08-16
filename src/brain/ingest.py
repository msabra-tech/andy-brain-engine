from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo
from .codex_integration import invoke_codex_for_source
from .config import Config, RuntimeConfig
from .hashing import sha256_file, hash_text
from .metadata import SourceMetadata, parse_capture
from .publish import publish
from .reminders import stable_action_id, upsert_actions
from .state import read_json, write_json


SUPPORTED_SUFFIXES = {".txt", ".md", ".json"}
TEMP_SUFFIXES = {".tmp", ".part", ".download", ".crdownload", ".icloud"}
CAPTURE_RE = re.compile(r"<!-- HB_CAPTURE_START (?P<meta>.*?) -->(?P<body>.*?)<!-- HB_CAPTURE_END -->", re.S)
SOURCE_DIR = "References/Sources"
SOURCE_INDEX = "References/Source Notes.md"
OPEN_QUESTIONS = "References/Open Questions.md"


@dataclass(frozen=True)
class Candidate:
    path: Path
    supported: bool
    stable: bool
    reason: str = ""


def is_ignored(path: Path) -> bool:
    if any(part.startswith(".") for part in path.parts):
        return True
    if path.name.startswith("~") or path.name.endswith("~"):
        return True
    if path.suffix.lower() in TEMP_SUFFIXES:
        return True
    return False


def snapshot(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


def snapshots_match(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a == b


def is_stable(path: Path, wait_seconds: int) -> bool:
    before = snapshot(path)
    if wait_seconds > 0:
        time.sleep(wait_seconds)
    after = snapshot(path)
    return snapshots_match(before, after)


def scan_inbox(config: Config, runtime: RuntimeConfig, wait: bool = True) -> list[Candidate]:
    root = config.bridge / "inbox"
    candidates: list[Candidate] = []
    if not root.exists():
        return candidates
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root)
        if is_ignored(rel) or path.name.endswith(".manifest.json"):
            continue
        stable = is_stable(path, runtime.stable_wait_seconds if wait else 0)
        candidates.append(Candidate(path=path, supported=path.suffix.lower() in SUPPORTED_SUFFIXES, stable=stable, reason="" if stable else "file is still changing"))
    return candidates


def ingest(config: Config, runtime: RuntimeConfig, invoke_codex: bool = True, wait_for_stable: bool = True) -> dict[str, int]:
    state_path = config.engine / "data/state/processing.json"
    state = read_json(state_path, {"version": 1, "sources": {}, "last_run": None, "last_success": None, "last_error": None})
    sources = state.setdefault("sources", {})
    counts = {"processed": 0, "skipped": 0, "unstable": 0, "errors": 0}
    files_changed: set[str] = set()

    for candidate in scan_inbox(config, runtime, wait=wait_for_stable):
        if not candidate.stable:
            counts["unstable"] += 1
            continue
        try:
            digest = sha256_file(candidate.path)
        except OSError:
            counts["unstable"] += 1
            continue
        source_id = f"src-{digest[:16]}"
        existing = sources.get(digest)
        if existing and existing.get("status") == "success":
            counts["skipped"] += 1
            _write_receipt(config, candidate.path, source_id, digest, "duplicate-skipped", [])
            continue
        archived = None
        try:
            archived = _archive_file(config, candidate.path, source_id, digest)
            if candidate.supported:
                text = candidate.path.read_text(encoding="utf-8")
                metadata, body = parse_capture(candidate.path, text, runtime.default_timezone)
            else:
                metadata = _binary_metadata(candidate.path, runtime)
                body = ""
            codex_result = {"ok": True, "skipped": True, "reason": "unsupported binary or Codex disabled for test"}
            if candidate.supported and invoke_codex:
                codex_result = invoke_codex_for_source(config, runtime, candidate.path, source_id, digest)
                if not codex_result.get("ok"):
                    raise RuntimeError("noninteractive Codex failed")
            changed, actions = _apply_source(config, runtime, candidate.path, source_id, digest, metadata, body, candidate.supported)
            if actions:
                _set_action_ids(actions, source_id, str(candidate.path.relative_to(config.bridge)))
                upsert_actions(config, actions)
                changed.add("bridge/outbox/reminders/actions.json")
            files_changed.update(changed)
            sources[digest] = {
                "source_id": source_id,
                "bridge_path": str(candidate.path.relative_to(config.bridge)),
                "archive_path": str(archived.relative_to(config.engine)),
                "status": "success",
                "processed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "files_changed": sorted(changed),
                "codex": codex_result,
            }
            _write_receipt(config, candidate.path, source_id, digest, "processed", sorted(changed), actions=actions, codex=codex_result)
            counts["processed"] += 1
        except OSError:
            counts["unstable"] += 1
            continue
        except Exception as exc:
            sources[digest] = {
                "source_id": source_id,
                "bridge_path": str(candidate.path.relative_to(config.bridge)),
                "archive_path": str(archived.relative_to(config.engine)) if archived else None,
                "status": "error",
                "error": str(exc),
                "processed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            }
            _write_receipt(config, candidate.path, source_id, digest, "error", [], error=str(exc))
            _write_error(config, candidate.path, source_id, digest, str(exc))
            counts["errors"] += 1

    quick_count, quick_changed = _process_quick_capture(config, runtime, state)
    counts["processed"] += quick_count
    files_changed.update(quick_changed)
    state["last_run"] = dt.datetime.now(dt.timezone.utc).isoformat()
    state["last_success"] = state["last_run"] if counts["errors"] == 0 else state.get("last_success")
    state["last_error"] = None if counts["errors"] == 0 else f"{counts['errors']} source(s) failed"
    state["last_files_changed"] = sorted(files_changed)
    write_json(state_path, state)
    if runtime.automatic_publishing:
        publish(config)
    return counts


def _binary_metadata(path: Path, runtime: RuntimeConfig) -> SourceMetadata:
    captured_at = dt.datetime.fromtimestamp(path.stat().st_mtime, ZoneInfo(runtime.default_timezone)).isoformat()
    return SourceMetadata(
        captured_at=captured_at,
        timestamp_basis="filesystem-arrival",
        temporal_confidence="low",
        timezone=runtime.default_timezone,
        source_type=f"unsupported {path.suffix.lower() or 'binary'}",
        source_title=path.name,
        speaker=None,
    )


def _archive_file(config: Config, path: Path, source_id: str, digest: str) -> Path:
    dst = config.engine / "data/raw" / dt.date.today().isoformat() / f"{source_id}-{path.name}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        shutil.copy2(path, dst)
    if sha256_file(dst) != digest:
        raise RuntimeError(f"archive hash mismatch for {path.name}")
    _upsert_archive_manifest(config, dst, digest, source_id)
    return dst


def _upsert_archive_manifest(config: Config, path: Path, digest: str, source_id: str) -> None:
    manifest_path = config.engine / "data/state/archive_manifest.json"
    manifest = read_json(manifest_path, {"version": 1, "items": []})
    items = {item["sha256"]: item for item in manifest.get("items", [])}
    items[digest] = {"path": path.relative_to(config.engine).as_posix(), "sha256": digest, "source_id": source_id}
    manifest["items"] = sorted(items.values(), key=lambda item: item["sha256"])
    write_json(manifest_path, manifest)


def _apply_source(config: Config, runtime: RuntimeConfig, path: Path, source_id: str, digest: str, metadata: SourceMetadata, body: str, supported: bool) -> tuple[set[str], list[dict]]:
    changed: set[str] = set()
    _write_source_card(config, source_id, digest, metadata, supported, "DEMO FIXTURE" in body)
    changed.add(f"{SOURCE_DIR}/{source_id}.md")
    _update_source_index(config, source_id, metadata)
    changed.add(SOURCE_INDEX)
    actions: list[dict] = []
    if not supported:
        _append_review(config, f"`REVIEW`: `{metadata.source_title}` was archived but awaits extraction or transcription. Source: [[{SOURCE_DIR}/{source_id}|{metadata.source_title}]].")
        changed.add(OPEN_QUESTIONS)
        return changed, actions
    if "DEMO FIXTURE" in body:
        demo_changed, actions = _apply_demo_fixture(config, runtime, source_id, digest, metadata, body)
        changed.update(demo_changed)
        return changed, actions
    real_changed, actions = _apply_real_text(config, runtime, source_id, metadata, body)
    changed.update(real_changed)
    return changed, actions


def _write_source_card(config: Config, source_id: str, digest: str, metadata: SourceMetadata, supported: bool, demo: bool) -> None:
    card = config.vault / SOURCE_DIR / f"{source_id}.md"
    card.parent.mkdir(parents=True, exist_ok=True)
    summary = "Captured example used for testing. Keep it separate from real notes." if demo else ("Captured text was archived locally before being organized." if supported else "Unsupported file was archived locally and awaits extraction or transcription.")
    privacy = "example" if demo else "private"
    card.write_text(f"""---
type: source-card
source_id: {source_id}
source_type: {metadata.source_type}
captured_at: {metadata.captured_at}
processing_date: {dt.date.today().isoformat()}
raw_evidence_available_locally: true
privacy: {privacy}
---

# {metadata.source_title}

## Source

- Source ID: `{source_id}`
- Source type: {metadata.source_type}
- Captured at: {metadata.captured_at}
- Timestamp basis: {metadata.timestamp_basis}
- Temporal confidence: {metadata.temporal_confidence}
- Content hash: `{digest}`
- Privacy classification: {privacy}

## Summary

{summary}

## Linked Notes

- [[References/Open Questions]]
- [[Home]]
- [[Today]]
""", encoding="utf-8")


def _update_source_index(config: Config, source_id: str, metadata: SourceMetadata) -> None:
    path = config.vault / SOURCE_INDEX
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.exists() else "# Source Notes\n\n"
    line = f"- [[{SOURCE_DIR}/{source_id}|{metadata.source_title}]] - {metadata.source_type}; captured at {metadata.captured_at}; temporal confidence: {metadata.temporal_confidence}."
    if line not in text:
        text = text.rstrip() + "\n" + line + "\n"
        path.write_text(text, encoding="utf-8")


def _apply_demo_fixture(config: Config, runtime: RuntimeConfig, source_id: str, digest: str, metadata: SourceMetadata, body: str) -> tuple[set[str], list[dict]]:
    return set(), []


def _apply_real_text(config: Config, runtime: RuntimeConfig, source_id: str, metadata: SourceMetadata, body: str) -> tuple[set[str], list[dict]]:
    changed: set[str] = set()
    tasks = _parse_labeled_items(body, ("Task", "To-do", "Todo"))
    shopping_items = _parse_shopping(body)
    ideas = _parse_labeled_items(body, ("Idea",))
    conversational_idea = _parse_idea(body)
    if conversational_idea:
        ideas.append(conversational_idea)
    reminders = _parse_reminders(body, metadata, runtime)
    work_items = _parse_labeled_items(body, ("Work", "Work note"))
    career_items = _parse_labeled_items(body, ("Career", "Career note"))
    project_items = _parse_labeled_items(body, ("Project", "Project note"))
    people_items = _parse_labeled_items(body, ("Person", "People"))
    reflections = _parse_labeled_items(body, ("Reflection",))
    decisions = _parse_labeled_items(body, ("Decision",))
    questions = _parse_labeled_items(body, ("Question", "Open question"))
    if tasks:
        path = config.vault / "Life/Tasks.md"
        text = path.read_text(encoding="utf-8")
        for task in tasks:
            text = _ensure_checkbox(text, task, False, f"Source: [[{SOURCE_DIR}/{source_id}|{metadata.source_title}]].")
        path.write_text(text, encoding="utf-8")
        changed.add("Life/Tasks.md")
    if shopping_items:
        path = config.vault / "Life/Shopping.md"
        text = path.read_text(encoding="utf-8")
        for item in shopping_items:
            text = _ensure_checkbox(text, item.title(), False, f"Source: [[{SOURCE_DIR}/{source_id}|{metadata.source_title}]].")
        path.write_text(text, encoding="utf-8")
        changed.add("Life/Shopping.md")
    if ideas:
        _append_source_bullets(config, "Ideas/Ideas Index.md", "Captured Ideas", ideas, source_id, metadata)
        changed.add("Ideas/Ideas Index.md")
    if work_items:
        _append_source_bullets(config, "Work/Work Notes.md", "Captured Work Notes", work_items, source_id, metadata)
        changed.add("Work/Work Notes.md")
    if career_items:
        _append_source_bullets(config, "Career/Career Notes.md", "Captured Career Notes", career_items, source_id, metadata)
        changed.add("Career/Career Notes.md")
    if project_items:
        _append_source_bullets(config, "Projects/Active Projects.md", "Captured Project Notes", project_items, source_id, metadata)
        changed.add("Projects/Active Projects.md")
    if people_items:
        _append_source_bullets(config, "People/People Index.md", "Captured Mentions", people_items, source_id, metadata)
        changed.add("People/People Index.md")
    if reflections:
        _append_source_bullets(config, "Life/Reflections.md", "Captured Reflections", reflections, source_id, metadata)
        changed.add("Life/Reflections.md")
    if decisions:
        _append_source_bullets(config, "Life/Decisions.md", "Captured Decisions", decisions, source_id, metadata)
        changed.add("Life/Decisions.md")
    if questions:
        for question in questions:
            _append_review(config, f"`REVIEW`: {_sentence(question)} Source: [[{SOURCE_DIR}/{source_id}|{metadata.source_title}]].")
        changed.add(OPEN_QUESTIONS)
    return changed, reminders


def _append_source_bullets(config: Config, rel_path: str, section: str, items: list[str], source_id: str, metadata: SourceMetadata) -> None:
    if not items:
        return
    path = config.vault / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.exists() else f"# {path.stem}\n\nSource boundary: created from captured notes only.\n"
    heading = f"## {section}"
    if heading not in text:
        text = text.rstrip() + f"\n\n{heading}\n"
    for item in _unique(items):
        line = f"- {_sentence(item)} Source: [[{SOURCE_DIR}/{source_id}|{metadata.source_title}]]."
        if line not in text:
            text = text.rstrip() + "\n" + line + "\n"
    path.write_text(text, encoding="utf-8")


def _parse_shopping(text: str) -> list[str]:
    match = re.search(r"Shopping list:\s*add\s+(.+?)(?:\.|\n|$)", text, re.I)
    if not match:
        return []
    raw = match.group(1).replace(" and ", ", ")
    return [item.strip(" .") for item in raw.split(",") if item.strip(" .")]


def _parse_labeled_items(text: str, labels: tuple[str, ...]) -> list[str]:
    label_alt = "|".join(re.escape(label) for label in labels)
    pattern = re.compile(rf"^\s*(?:[-*]\s*)?(?:\[[ xX]\]\s*)?(?:{label_alt})\s*:\s*(.+?)\s*$", re.I | re.M)
    return _unique(_clean_item(item) for item in pattern.findall(text))


def _parse_prefixed(text: str, prefix: str) -> str | None:
    match = re.search(re.escape(prefix) + r"\s*(.+?)(?:\.|\n|$)", text, re.I)
    return match.group(1).strip() if match else None


def _parse_idea(text: str) -> str | None:
    match = re.search(r"I have an idea to\s+(.+?)(?:\.|\n|$)", text, re.I)
    return match.group(1).strip() if match else None


def _parse_reminders(text: str, metadata: SourceMetadata, runtime: RuntimeConfig) -> list[dict]:
    reminders: list[dict] = []
    for match in re.finditer(r"Remind me at\s+(.+?)\s+to\s+(.+?)(?:\.|\n|$)", text, re.I):
        action = _build_reminder(match.group(1), match.group(2), metadata, runtime)
        if action:
            reminders.append(action)
    for item in _parse_labeled_items(text, ("Reminder",)):
        if re.search(r"Remind me at\s+", item, re.I):
            continue
        match = re.match(r"(.+?)\s*(?:-| to )\s*(.+)$", item, re.I)
        if match:
            action = _build_reminder(match.group(1), match.group(2), metadata, runtime)
            if action:
                reminders.append(action)
    return _unique_actions(reminders)


def _parse_reminder(text: str, metadata: SourceMetadata, runtime: RuntimeConfig) -> dict | None:
    reminders = _parse_reminders(text, metadata, runtime)
    return reminders[0] if reminders else None


def _build_reminder(when_raw: str, title: str, metadata: SourceMetadata, runtime: RuntimeConfig) -> dict | None:
    when_raw = when_raw.strip()
    title = _clean_item(title)
    due = _resolve_due(when_raw, metadata, runtime)
    if not due:
        return None
    source_id = "pending"
    return {
        "operation": "create",
        "title": title,
        "due": due,
        "all_day": False,
        "list": runtime.reminder_list,
        "notes": f"Source captured at {metadata.captured_at}. Timestamp basis: {metadata.timestamp_basis}.",
        "source_id": source_id,
        "source_path": metadata.source_title,
        "confidence": "high" if metadata.temporal_confidence == "high" else "required-review",
        "approval": "automatic" if metadata.temporal_confidence == "high" else "required-review",
        "status": "planned",
    }


def _resolve_due(raw: str, metadata: SourceMetadata, runtime: RuntimeConfig) -> str | None:
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo(runtime.default_timezone))
        return parsed.isoformat()
    except ValueError:
        pass
    time_match = re.match(r"^(\d{1,2}):(\d{2})$", raw)
    if time_match:
        base = dt.datetime.fromisoformat(metadata.captured_at)
        hour, minute = int(time_match.group(1)), int(time_match.group(2))
        due = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if due <= base:
            due = due + dt.timedelta(days=1)
        return due.isoformat()
    return None


def _clean_item(item: str) -> str:
    return item.strip().strip("-* ").strip()


def _sentence(item: str) -> str:
    clean = _clean_item(item)
    if clean.endswith((".", "?", "!")):
        return clean
    return f"{clean}."


def _unique(items) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        clean = _clean_item(item)
        key = clean.casefold()
        if clean and key not in seen:
            seen.add(key)
            result.append(clean)
    return result


def _unique_actions(actions: list[dict]) -> list[dict]:
    seen: set[tuple[str, str | None]] = set()
    result: list[dict] = []
    for action in actions:
        key = (action["title"].casefold(), action.get("due"))
        if key not in seen:
            seen.add(key)
            result.append(action)
    return result


def _set_action_ids(actions: list[dict], source_id: str, source_path: str) -> list[dict]:
    for action in actions:
        action["source_id"] = source_id
        action["source_path"] = source_path
        action["action_id"] = stable_action_id(action["operation"], action["title"], action.get("due"), source_id)
    return actions


def _ensure_checkbox(text: str, label: str, checked: bool, suffix: str) -> str:
    state = "x" if checked else " "
    pattern = re.compile(rf"^- \[[ xX]\] {re.escape(label)}(?:\.|\\b).*?$", re.M)
    replacement = f"- [{state}] {label}. {suffix}"
    if pattern.search(text):
        return pattern.sub(replacement, text, count=1)
    return text.rstrip() + "\n" + replacement + "\n"


def _complete_named_item(text: str, label: str, suffix: str) -> str:
    return _ensure_checkbox(text, label, True, suffix)


def _replace_block(text: str, marker: str, block: str) -> str:
    start = f"<!-- {marker}:START -->"
    end = f"<!-- {marker}:END -->"
    wrapped = f"{start}\n{block}\n{end}"
    if start in text and end in text:
        return re.sub(re.escape(start) + r".*?" + re.escape(end), wrapped, text, flags=re.S)
    return text.rstrip() + "\n\n" + wrapped + "\n"


def _append_review(config: Config, line: str) -> None:
    path = config.vault / OPEN_QUESTIONS
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.exists() else "# Open Questions\n"
    if line not in text:
        path.write_text(text.rstrip() + "\n- " + line + "\n", encoding="utf-8")


def _write_receipt(config: Config, path: Path, source_id: str, digest: str, status: str, files_changed: list[str], actions: list[dict] | None = None, codex: dict | None = None, error: str | None = None) -> None:
    payload = {
        "version": 1,
        "source_id": source_id,
        "source_hash": digest,
        "source_name": path.name,
        "source_path": str(path.relative_to(config.bridge)),
        "status": status,
        "processed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "files_changed": files_changed,
        "validation_result": "not-run",
    }
    if actions is not None:
        payload["reminder_actions"] = [action.get("action_id") for action in actions]
    if codex is not None:
        payload["codex"] = codex
    if error:
        payload["error"] = error
    write_json(config.bridge / "receipts/ingestion" / f"{digest[:16]}.json", payload)


def _write_error(config: Config, path: Path, source_id: str, digest: str, error: str) -> None:
    write_json(config.bridge / "errors" / f"{digest[:16]}.json", {
        "version": 1,
        "source_id": source_id,
        "source_hash": digest,
        "source_path": str(path.relative_to(config.bridge)),
        "error": error,
        "at": dt.datetime.now(dt.timezone.utc).isoformat(),
    })


def _process_quick_capture(config: Config, runtime: RuntimeConfig, state: dict) -> tuple[int, set[str]]:
    path = config.vault / "Quick Capture.md"
    if not path.exists():
        return 0, set()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return 0, set()
    changed: set[str] = set()
    count = 0
    for match in CAPTURE_RE.finditer(text):
        raw = match.group(0)
        digest = hash_text(raw)
        if digest in state.setdefault("sources", {}):
            continue
        source_id = f"quick-{digest[:16]}"
        archive = config.engine / "data/raw" / dt.date.today().isoformat() / f"{source_id}-quick-capture.md"
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_text(raw, encoding="utf-8")
        state["sources"][digest] = {
            "source_id": source_id,
            "bridge_path": "Quick Capture.md",
            "archive_path": str(archive.relative_to(config.engine)),
            "status": "success",
            "processed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        changed.add("Quick Capture.md")
        count += 1
    return count, changed
