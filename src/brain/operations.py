from __future__ import annotations

import datetime as dt
import json
import re
import shutil
from pathlib import Path
from typing import Any

from .config import Config
from .state import read_json, write_json


COMMAND_CENTER = Path("00 Command Center")
WORKSTREAMS = Path("10 Active Work")
PEOPLE = Path("20 People")
INSIGHTS = Path("30 Decisions and Insights")
HANDOFFS = Path("40 Chat Handoffs")
ARCHIVE = Path("90 Archive")
PROPOSALS_PATH = Path("data/state/proposals.json")
PRESENTATION_PATH = Path("config/presentation.local.json")
PRIORITIES = {"Critical", "High", "Normal", "Low"}


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-")
    return slug or "Untitled-Workstream"


def markdown_escape(value: str) -> str:
    return value.replace("\r", " ").replace("\n", " ").strip()


def vault_path(config: Config, relative: Path | str) -> Path:
    return config.vault / Path(relative)


def ensure_layout(config: Config) -> None:
    for folder in [COMMAND_CENTER, WORKSTREAMS, PEOPLE, INSIGHTS, HANDOFFS, ARCHIVE]:
        vault_path(config, folder).mkdir(parents=True, exist_ok=True)


def _read_proposals(config: Config) -> dict[str, Any]:
    return read_json(config.engine / PROPOSALS_PATH, {"version": 1, "proposals": []})


def _write_proposals(config: Config, payload: dict[str, Any]) -> None:
    write_json(config.engine / PROPOSALS_PATH, payload)


def _proposal_id(payload: dict[str, Any]) -> str:
    return f"proposal-{len(payload.get('proposals', [])) + 1:04d}"


def create_proposal(config: Config, kind: str, summary: str, payload: dict[str, Any]) -> dict[str, Any]:
    state = _read_proposals(config)
    proposal = {
        "id": _proposal_id(state),
        "kind": kind,
        "summary": markdown_escape(summary),
        "payload": payload,
        "status": "proposed",
        "created_at": now(),
        "applied_at": None,
    }
    state.setdefault("proposals", []).append(proposal)
    _write_proposals(config, state)
    return proposal


def list_proposals(config: Config, include_closed: bool = False) -> list[dict[str, Any]]:
    proposals = _read_proposals(config).get("proposals", [])
    if include_closed:
        return proposals
    return [proposal for proposal in proposals if proposal.get("status") == "proposed"]


def _find_proposal(config: Config, proposal_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    state = _read_proposals(config)
    for proposal in state.get("proposals", []):
        if proposal.get("id") == proposal_id:
            return state, proposal
    raise KeyError(f"unknown proposal: {proposal_id}")


def workstream_relative(title: str) -> Path:
    return WORKSTREAMS / slugify(title)


def _frontmatter(metadata: dict[str, str]) -> str:
    rows = ["---"]
    rows.extend(f"{key}: {markdown_escape(value)}" for key, value in metadata.items())
    rows.append("---")
    return "\n".join(rows)


def _read_frontmatter(path: Path) -> tuple[dict[str, str], str]:
    if not path.exists():
        return {}, ""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    metadata: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            metadata[key.strip()] = value.strip()
    return metadata, text[end + 5 :]


def _write_frontmatter(path: Path, updates: dict[str, str], body: str | None = None) -> None:
    metadata, existing_body = _read_frontmatter(path)
    metadata.update({key: markdown_escape(value) for key, value in updates.items()})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_frontmatter(metadata) + "\n\n" + (existing_body if body is None else body).lstrip(), encoding="utf-8")


def _append(path: Path, entry: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(f"# {path.stem}\n", encoding="utf-8")
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n" + entry.rstrip() + "\n")


def ensure_workstream(config: Config, title: str, priority: str = "Normal", status: str = "active") -> Path:
    ensure_layout(config)
    relative = workstream_relative(title)
    root = vault_path(config, relative)
    root.mkdir(parents=True, exist_ok=True)
    overview = root / "Overview.md"
    if not overview.exists():
        overview.write_text(
            _frontmatter(
                {
                    "type": "workstream",
                    "title": title,
                    "status": status,
                    "priority": priority,
                    "priority_score": "",
                    "priority_reasoning": "",
                    "updated": now(),
                    "andy_override": "",
                    "andy_priority_score_override": "",
                    "andy_priority_reasoning": "",
                }
            )
            + f"\n\n# {title}\n\n## Current understanding\n\n_No approved summary yet._\n\n## Navigation\n\n"
            + "\n".join(f"- [[{(relative / section).as_posix()}|{section}]]" for section in presentation_profile(config).get("workstream_sections", ["Updates", "Open Threads", "Research", "Sources"]))
            + "\n",
            encoding="utf-8",
        )
    headings = {"Updates": "Updates", "Open Threads": "Open Threads", "Research": "Research", "Sources": "Source Links"}
    for name in presentation_profile(config).get("workstream_sections", ["Updates", "Open Threads", "Research", "Sources"]):
        heading = headings.get(name, name)
        target = root / f"{name}.md"
        if not target.exists():
            target.write_text(f"# {heading}\n", encoding="utf-8")
    return relative


def propose_workstream_update(
    config: Config,
    *,
    title: str,
    summary: str,
    priority: str = "Normal",
    priority_score: int | None = None,
    priority_reasoning: str = "",
    recommendation: str = "",
    people: list[str] | None = None,
    source_links: list[str] | None = None,
    open_threads: list[str] | None = None,
    follow_ups: list[dict[str, str]] | None = None,
    research: str = "",
) -> dict[str, Any]:
    normalized_priority = markdown_escape(priority).title()
    if normalized_priority not in PRIORITIES:
        raise ValueError(f"priority must be one of: {', '.join(sorted(PRIORITIES))}")
    if priority_score is not None and not 0 <= int(priority_score) <= 100:
        raise ValueError("priority_score must be between 0 and 100")
    normalized_follow_ups = [
        {
            "text": markdown_escape(str(item.get("text", ""))),
            "due_date": markdown_escape(str(item.get("due_date", ""))),
            "reason": markdown_escape(str(item.get("reason", ""))),
        }
        for item in follow_ups or []
        if str(item.get("text", "")).strip()
    ]
    payload = {
        "title": markdown_escape(title),
        "summary": summary.strip(),
        "priority": normalized_priority,
        "priority_score": int(priority_score) if priority_score is not None else None,
        "priority_reasoning": priority_reasoning.strip(),
        "recommendation": recommendation.strip(),
        "people": [markdown_escape(person) for person in people or [] if person.strip()],
        "source_links": [link.strip() for link in source_links or [] if link.strip()],
        "open_threads": [thread.strip() for thread in open_threads or [] if thread.strip()],
        "follow_ups": normalized_follow_ups,
        "research": research.strip(),
    }
    return create_proposal(config, "workstream_update", f"Update {payload['title']}", payload)


def _ensure_person(config: Config, name: str) -> Path:
    relative = PEOPLE / f"{slugify(name)}.md"
    path = vault_path(config, relative)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            _frontmatter({"type": "person", "name": name, "updated": now()})
            + f"\n\n# {name}\n\n## Context\n\n_No approved context yet._\n\n## Linked work\n",
            encoding="utf-8",
        )
    return relative


def _apply_workstream_update(config: Config, payload: dict[str, Any]) -> list[str]:
    relative = ensure_workstream(config, payload["title"], payload.get("priority", "Normal"))
    root = vault_path(config, relative)
    overview = root / "Overview.md"
    metadata, body = _read_frontmatter(overview)
    if not metadata.get("andy_override"):
        metadata["priority"] = payload.get("priority", "Normal")
        if payload.get("priority_score") is not None:
            metadata["priority_score"] = str(payload["priority_score"])
        if payload.get("priority_reasoning"):
            metadata["priority_reasoning"] = payload["priority_reasoning"]
    metadata["updated"] = now()
    _write_frontmatter(overview, metadata, body)

    stamp = now()
    update = f"## {stamp}\n\n{payload['summary']}"
    if payload.get("recommendation"):
        update += f"\n\n**Claude recommendation:** {payload['recommendation']}"
    _append(root / "Updates.md", update)

    if payload.get("open_threads") or payload.get("follow_ups"):
        lines = [f"- [ ] {item}" for item in payload.get("open_threads", [])]
        for follow_up in payload.get("follow_ups", []):
            due = f" _(due {follow_up['due_date']})_" if follow_up.get("due_date") else ""
            reason = f" — {follow_up['reason']}" if follow_up.get("reason") else ""
            lines.append(f"- [ ] {follow_up['text']}{due}{reason}")
        _append(root / "Open Threads.md", "\n".join(lines))
    if payload.get("research"):
        _append(root / "Research.md", f"## {stamp}\n\n{payload['research']}")
    if payload.get("source_links"):
        _append(root / "Sources.md", "\n".join(f"- {item}" for item in payload["source_links"]))

    changed = [str(relative / "Overview.md"), str(relative / "Updates.md")]
    for person in payload.get("people", []):
        person_relative = _ensure_person(config, person)
        link = f"- [[{relative.as_posix()}|{payload['title']}]]"
        person_path = vault_path(config, person_relative)
        if link not in person_path.read_text(encoding="utf-8"):
            _append(person_path, link)
        changed.append(str(person_relative))
    return changed


def propose_priority_override(
    config: Config,
    *,
    title: str,
    priority: str,
    priority_score: int | None = None,
    reasoning: str = "",
) -> dict[str, Any]:
    normalized_priority = markdown_escape(priority).title()
    if normalized_priority not in PRIORITIES:
        raise ValueError(f"priority must be one of: {', '.join(sorted(PRIORITIES))}")
    if priority_score is not None and not 0 <= int(priority_score) <= 100:
        raise ValueError("priority_score must be between 0 and 100")
    return create_proposal(
        config,
        "priority_override",
        f"Set Andy's priority override for {markdown_escape(title)}",
        {"title": markdown_escape(title), "priority": normalized_priority, "priority_score": int(priority_score) if priority_score is not None else None, "reasoning": reasoning.strip()},
    )


def _apply_priority_override(config: Config, payload: dict[str, Any]) -> list[str]:
    relative = ensure_workstream(config, payload["title"], payload["priority"])
    overview = vault_path(config, relative / "Overview.md")
    metadata, body = _read_frontmatter(overview)
    metadata["andy_override"] = payload["priority"]
    metadata["andy_priority_score_override"] = "" if payload.get("priority_score") is None else str(payload["priority_score"])
    metadata["andy_priority_reasoning"] = payload.get("reasoning", "")
    metadata["updated"] = now()
    _write_frontmatter(overview, metadata, body)
    _append(
        vault_path(config, relative / "Updates.md"),
        f"## {now()}\n\n**Andy priority override:** {payload['priority']}"
        + (f" · {payload['priority_score']}/100" if payload.get("priority_score") is not None else "")
        + (f"\n\n**Reason:** {payload['reasoning']}" if payload.get("reasoning") else ""),
    )
    return [str(relative / "Overview.md"), str(relative / "Updates.md")]


def propose_research_update(
    config: Config,
    *,
    title: str,
    question: str,
    findings: str,
    evidence_links: list[str] | None = None,
    recommendation: str = "",
    next_steps: list[str] | None = None,
) -> dict[str, Any]:
    return create_proposal(
        config,
        "research_update",
        f"Add research to {markdown_escape(title)}",
        {
            "title": markdown_escape(title),
            "question": question.strip(),
            "findings": findings.strip(),
            "evidence_links": [item.strip() for item in evidence_links or [] if item.strip()],
            "recommendation": recommendation.strip(),
            "next_steps": [item.strip() for item in next_steps or [] if item.strip()],
        },
    )


def _apply_research_update(config: Config, payload: dict[str, Any]) -> list[str]:
    relative = ensure_workstream(config, payload["title"])
    profile = presentation_profile(config)
    research_section = profile.get("research_target") or ("Research" if "Research" in profile.get("workstream_sections", []) else "Updates")
    research = vault_path(config, relative / f"{research_section}.md")
    entry = f"## {now()} · {payload['question']}\n\n{payload['findings']}"
    if payload.get("evidence_links"):
        entry += "\n\n### Evidence\n\n" + "\n".join(f"- {item}" for item in payload["evidence_links"])
    if payload.get("recommendation"):
        entry += f"\n\n### Claude recommendation\n\n{payload['recommendation']}"
    if payload.get("next_steps"):
        entry += "\n\n### Suggested next steps\n\n" + "\n".join(f"- [ ] {item}" for item in payload["next_steps"])
    _append(research, entry)
    return [str(relative / f"{research_section}.md")]


def propose_external_write(
    config: Config,
    *,
    connector: str,
    title: str,
    content: str,
    target: str = "",
) -> dict[str, Any]:
    if connector not in {"google_drive", "notion", "local_file"}:
        raise ValueError("connector must be google_drive, notion, or local_file")
    if not title.strip() or not content.strip():
        raise ValueError("external-write proposals require a title and content")
    return create_proposal(
        config,
        "external_write",
        f"Write '{markdown_escape(title)}' to {connector} after Andy approval",
        {"connector": connector, "title": markdown_escape(title), "content": content, "target": target.strip(), "approval_required": True},
    )


def _apply_external_write(config: Config, payload: dict[str, Any]) -> list[str]:
    from .connectors import write_external_artifact

    result = write_external_artifact(config, payload)
    location = result.get("url") or result.get("path") or result.get("id") or payload["title"]
    return [f"{result.get('connector', payload['connector'])}: {location}"]


def propose_system_change(config: Config, *, title: str, summary: str, patch: str) -> dict[str, Any]:
    """Stage a Claude-authored code change in an isolated workspace before approval."""
    proposal = create_proposal(
        config,
        "system_change",
        summary,
        {"title": markdown_escape(title), "patch": patch, "preflight": None, "approval_required": True},
    )
    from .system_evolution import stage_system_change

    preflight = stage_system_change(config, proposal["id"], patch)
    state, stored = _find_proposal(config, proposal["id"])
    stored["payload"]["preflight"] = preflight
    _write_proposals(config, state)
    return stored


def _apply_system_change(config: Config, proposal_id: str, payload: dict[str, Any]) -> list[str]:
    from .system_evolution import install_system_change

    result = install_system_change(config, proposal_id, payload["patch"], payload.get("preflight") or {})
    return [f"engine backup: {result['backup']}", *result.get("affected_files", [])]


def save_chat_handoff(
    config: Config,
    *,
    objective: str,
    summary: str,
    workstreams: list[str] | None = None,
    decisions: list[str] | None = None,
    open_questions: list[str] | None = None,
    next_prompt: str = "",
) -> list[str]:
    ensure_layout(config)
    stamp = now()
    body = f"# Current Context\n\n**Updated:** {stamp}\n\n## Objective\n\n{objective.strip()}\n\n## Summary\n\n{summary.strip()}\n\n## Active workstreams\n\n"
    body += "\n".join(f"- [[{workstream_relative(item).as_posix()}|{item}]]" for item in workstreams or []) or "- _None recorded._"
    body += "\n\n## Decisions\n\n" + ("\n".join(f"- {item}" for item in decisions or []) or "- _None recorded._")
    body += "\n\n## Open questions\n\n" + ("\n".join(f"- {item}" for item in open_questions or []) or "- _None recorded._")
    body += f"\n\n## Continue from here\n\n{next_prompt.strip() or 'Continue my Andy Brain from this handoff.'}\n"
    current = vault_path(config, HANDOFFS / "Current Context.md")
    current.parent.mkdir(parents=True, exist_ok=True)
    current.write_text(body, encoding="utf-8")
    history = vault_path(config, HANDOFFS / "History" / f"{stamp.replace(':', '-').replace('+', '-')}.md")
    history.parent.mkdir(parents=True, exist_ok=True)
    history.write_text(body.replace("# Current Context", "# Chat Handoff"), encoding="utf-8")
    return [str(HANDOFFS / "Current Context.md"), str(history.relative_to(config.vault))]


def presentation_profile(config: Config) -> dict[str, Any]:
    default = {
        "version": 1,
        "home_sections": ["needs_attention", "michael", "active_work", "open_threads", "decisions", "handoff"],
        "workstream_sections": ["Updates", "Open Threads", "Research", "Sources"],
        "labels": {"active_work": "Active Work", "decisions": "Decisions and Insights"},
    }
    path = config.engine / PRESENTATION_PATH
    if not path.exists():
        return default
    data = read_json(path, default)
    return {**default, **data}


def propose_presentation_change(config: Config, summary: str, profile_updates: dict[str, Any], migration: dict[str, Any] | None = None) -> dict[str, Any]:
    return create_proposal(
        config,
        "presentation_change",
        summary,
        {"profile_updates": profile_updates, "migration": migration or {}, "backup_required": True},
    )


def _backup_vault(config: Config) -> Path:
    target = config.engine / "data/backups/vault" / now().replace(":", "-")
    shutil.copytree(config.vault, target, ignore=shutil.ignore_patterns(".obsidian/workspace*.json"))
    return target


def _apply_presentation_change(config: Config, payload: dict[str, Any]) -> list[str]:
    profile = presentation_profile(config)
    profile.update(payload.get("profile_updates", {}))
    if payload.get("backup_required"):
        _backup_vault(config)
    migration = payload.get("migration", {})
    archived: list[str] = []
    for section in migration.get("archive_workstream_sections", []):
        safe_section = slugify(str(section)).replace("-", " ")
        for source in vault_path(config, WORKSTREAMS).glob(f"*/{safe_section}.md"):
            target = vault_path(config, ARCHIVE / "Presentation Migrations" / now().replace(":", "-") / source.relative_to(config.vault))
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
            archived.append(str(target.relative_to(config.vault)))
    write_json(config.engine / PRESENTATION_PATH, profile)
    return [str(PRESENTATION_PATH), *archived]


def apply_proposal(config: Config, proposal_id: str, confirmed: bool) -> dict[str, Any]:
    if not confirmed:
        raise PermissionError("explicit confirmation is required to apply a proposal")
    state, proposal = _find_proposal(config, proposal_id)
    if proposal.get("status") != "proposed":
        raise ValueError(f"proposal is already {proposal.get('status')}")
    if proposal["kind"] == "workstream_update":
        changed = _apply_workstream_update(config, proposal["payload"])
    elif proposal["kind"] == "priority_override":
        changed = _apply_priority_override(config, proposal["payload"])
    elif proposal["kind"] == "research_update":
        changed = _apply_research_update(config, proposal["payload"])
    elif proposal["kind"] == "external_write":
        changed = _apply_external_write(config, proposal["payload"])
    elif proposal["kind"] == "system_change":
        changed = _apply_system_change(config, proposal_id, proposal["payload"])
    elif proposal["kind"] == "presentation_change":
        changed = _apply_presentation_change(config, proposal["payload"])
    else:
        raise ValueError(f"proposal kind cannot be applied by the local engine: {proposal['kind']}")
    proposal["status"] = "applied"
    proposal["applied_at"] = now()
    proposal["changed"] = changed
    _write_proposals(config, state)
    return proposal
