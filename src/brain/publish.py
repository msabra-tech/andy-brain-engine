from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path
from .config import Config, RuntimeConfig
from .hashing import sha256_file
from .state import read_json


TEMP_SUFFIXES = {".tmp", ".part", ".download", ".crdownload", ".icloud"}


def _checkboxes(path: Path, limit: int = 6) -> list[str]:
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    return [line for line in text.splitlines() if line.startswith("- [")][:limit]


def _links(vault: Path, folder: Path, limit: int = 6) -> list[str]:
    if not folder.exists():
        return []
    links = []
    for path in sorted(folder.glob("*.md")):
        if path.name.startswith(".") or "Index" in path.stem:
            continue
        target = path.relative_to(vault).with_suffix("").as_posix()
        links.append(f"- [[{target}|{path.stem}]]")
    return links[:limit]


def _state(config: Config) -> dict:
    return read_json(config.engine / "data/state/processing.json", {"sources": {}, "last_run": None, "last_success": None, "last_error": None})


def _actions(config: Config) -> dict:
    try:
        return read_json(config.bridge / "outbox/reminders/actions.json", {"actions": []})
    except (OSError, json.JSONDecodeError):
        return {"actions": []}


def publish(config: Config, runtime: RuntimeConfig | None = None) -> None:
    vault = config.vault
    runtime = runtime or RuntimeConfig()
    today = dt.date.today().isoformat()
    state = _state(config)
    actions = _actions(config)
    pending = _pending_count(config, state)
    tasks = _checkboxes(vault / "Life/Tasks.md")
    reminders = _checkboxes(vault / "Life/Reminders.md")
    shopping = _checkboxes(vault / "Life/Shopping.md")
    projects = _links(vault, vault / "Projects")
    ideas = _links(vault, vault / "Ideas")
    people = _links(vault, vault / "People")
    career = _links(vault, vault / "Career")
    work = _links(vault, vault / "Work")
    open_questions = _review_count(vault / "References/Open Questions.md")
    planned_actions = _planned_action_count(actions)

    home = f"""---
type: home
updated: {today}
---

# {runtime.vault_title}

## Start Here

- [[Today|Today's view]]
- [[Projects/AI Second Brain|AI Second Brain project]]
- [[Life/Tasks|Tasks]]
- [[Life/Reminders|Reminders]]
- [[Ideas/Personal AI Second Brain|Personal AI Second Brain idea]]
- [[References/Open Questions|Open questions]]

## Life

- [[Life/Tasks|Tasks]]
- [[Life/Reminders|Reminders]]
- [[Life/Shopping|Shopping]]
- [[Life/Reflections|Reflections]]
- [[Life/Decisions|Decisions]]

## Work And Career

{chr(10).join(work + career) if work or career else "- _No work or career notes captured yet._"}

## Projects

{chr(10).join(projects) if projects else "- _No active projects captured yet._"}

## Ideas

{chr(10).join(ideas) if ideas else "- _No ideas captured yet._"}

## People

{chr(10).join(people) if people else "- _No people notes captured yet._"}

## Needs Attention

- [[References/Open Questions|Open questions]]: {open_questions} item(s).
- New captures waiting: {"yes" if pending else "no"}.
- Reminder items waiting: {planned_actions}.
"""
    _write_text(vault / "Home.md", home)

    today_text = f"""---
type: today
updated: {today}
---

# Today

## Must Do

{chr(10).join(tasks[:4]) if tasks else "- _No source-backed must-do items captured yet._"}

## Upcoming

{chr(10).join(reminders[:4]) if reminders else "- _No dated reminders captured yet._"}

## Life

{chr(10).join(shopping[:4]) if shopping else "- _No shopping items captured yet._"}

## Work And Career

{chr(10).join((work + career)[:4]) if work or career else "- _No work or career items captured yet._"}

## Projects

{chr(10).join(projects[:4]) if projects else "- _No active projects captured yet._"}

## Ideas Worth Revisiting

{chr(10).join(ideas[:4]) if ideas else "- _No ideas captured yet._"}

## Needs Attention

- [[References/Open Questions|Open questions]]
- [[References/Source Notes|Source notes]]
"""
    _write_text(vault / "Today.md", today_text)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    last_error: OSError | None = None
    for attempt in range(5):
        try:
            path.write_text(text, encoding="utf-8")
            return
        except OSError as exc:
            last_error = exc
            if attempt == 4:
                break
            time.sleep(1)
    raise last_error or OSError(f"could not write {path}")


def _pending_count(config: Config, state: dict | None = None) -> int:
    root = config.bridge / "inbox"
    if not root.exists():
        return 0
    successful = {
        digest
        for digest, record in (state or _state(config)).get("sources", {}).items()
        if isinstance(record, dict) and record.get("status") == "success"
    }
    count = 0
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if not path.is_file():
            continue
        if any(part.startswith(".") for part in rel.parts):
            continue
        if path.name.endswith(".manifest.json") or path.suffix.lower() in TEMP_SUFFIXES:
            continue
        try:
            digest = sha256_file(path)
        except OSError:
            count += 1
            continue
        if digest not in successful:
            count += 1
    return count


def _review_count(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return 0
    return sum(1 for line in text.splitlines() if line.startswith("- "))


def _planned_action_count(actions: dict) -> int:
    return sum(1 for action in actions.get("actions", []) if action.get("status") == "planned")
