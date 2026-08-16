from __future__ import annotations

import datetime as dt
from pathlib import Path

from .config import Config, RuntimeConfig
from .operations import COMMAND_CENTER, HANDOFFS, INSIGHTS, PEOPLE, WORKSTREAMS, _read_frontmatter, ensure_layout, presentation_profile, vault_path


def _link(relative: Path, label: str | None = None) -> str:
    target = relative.with_suffix("").as_posix()
    return f"[[{target}|{label or relative.stem}]]"


def _workstreams(config: Config) -> list[dict[str, str]]:
    root = vault_path(config, WORKSTREAMS)
    records: list[dict[str, str]] = []
    for overview in root.glob("*/Overview.md"):
        metadata, _ = _read_frontmatter(overview)
        relative = overview.relative_to(config.vault)
        records.append(
            {
                "title": metadata.get("title", overview.parent.name),
                "priority": metadata.get("andy_override") or metadata.get("priority", "Normal"),
                "status": metadata.get("status", "active"),
                "updated": metadata.get("updated", ""),
                "relative": relative.with_suffix(""),
            }
        )
    order = {"Critical": 0, "High": 1, "Normal": 2, "Low": 3}
    return sorted(records, key=lambda item: (order.get(item["priority"], 9), item["title"].lower()))


def _open_threads(config: Config, workstreams: list[dict[str, str]]) -> list[str]:
    items: list[str] = []
    for workstream in workstreams:
        source = vault_path(config, Path(workstream["relative"]).parent / "Open Threads.md")
        if not source.exists():
            continue
        for line in source.read_text(encoding="utf-8").splitlines():
            if line.startswith("- [ "):
                items.append(f"- {workstream['title']}: {line[6:]}")
    return items


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def publish_command_center(config: Config, runtime: RuntimeConfig | None = None) -> None:
    ensure_layout(config)
    runtime = runtime or RuntimeConfig()
    profile = presentation_profile(config)
    workstreams = _workstreams(config)
    open_threads = _open_threads(config, workstreams)
    high_priority = [item for item in workstreams if item["priority"] in {"Critical", "High"}]
    work_links = [f"- {_link(Path(item['relative']), item['title'])} · {item['priority']}" for item in workstreams]
    attention = [f"- {_link(Path(item['relative']), item['title'])} · {item['priority']} priority" for item in high_priority]
    handoff = HANDOFFS / "Current Context.md"
    current_handoff = _link(handoff, "Continue from the latest Claude handoff") if vault_path(config, handoff).exists() else "_No saved handoff yet._"

    labels = profile.get("labels", {})
    home = f"""---
type: command-center
updated: {dt.date.today().isoformat()}
---

# {runtime.vault_title} Command Center

## Needs Attention

{chr(10).join(attention) if attention else "- _No high-priority workstreams yet._"}

## Michael and leadership context

- [[20 People/Michael|Michael]]
- [[00 Command Center/Michael Meeting Prep|Michael Meeting Prep]]

## {labels.get('active_work', 'Active Work')}

{chr(10).join(work_links) if work_links else "- _No approved workstreams yet._"}

## Open threads

{chr(10).join(open_threads[:12]) if open_threads else "- _No open threads recorded._"}

## {labels.get('decisions', 'Decisions and Insights')}

- [[30 Decisions and Insights/Decisions|Decisions]]
- [[30 Decisions and Insights/Open Questions|Open Questions]]

## Continue working

{current_handoff}
"""
    _write(vault_path(config, COMMAND_CENTER / "Home.md"), home)
    _write(vault_path(config, "Home.md"), f"# {runtime.vault_title}\n\n- {_link(COMMAND_CENTER / 'Home.md', 'Open the Command Center')}\n")

    needs_attention = "# Needs Attention\n\n" + ("\n".join(attention + open_threads) if attention or open_threads else "_Nothing is currently flagged._")
    _write(vault_path(config, COMMAND_CENTER / "Needs Attention.md"), needs_attention)

    this_week = "# This Week\n\n## Active work\n\n" + ("\n".join(work_links) if work_links else "_No active work yet._")
    _write(vault_path(config, COMMAND_CENTER / "This Week.md"), this_week)

    michael = "# Michael Meeting Prep\n\n## Open work relevant to Michael\n\n" + ("\n".join(attention + open_threads) if attention or open_threads else "_Run a Claude review before the meeting._")
    michael += "\n\n## Suggested questions\n\n- What changed in priority since the last meeting?\n- Which open decisions need Michael’s input?\n"
    _write(vault_path(config, COMMAND_CENTER / "Michael Meeting Prep.md"), michael)

    defaults = {
        INSIGHTS / "Decisions.md": "# Decisions\n\n_Approved decisions captured from Claude reviews appear here._",
        INSIGHTS / "Open Questions.md": "# Open Questions\n\n_Questions that need evidence or a decision appear here._",
        PEOPLE / "Michael.md": "---\ntype: person\nname: Michael\n---\n\n# Michael\n\n## Context\n\n_Claude adds approved commitments and requests here._\n\n## Linked work\n",
        HANDOFFS / "Current Context.md": "# Current Context\n\n_Claude writes an approved handoff here at the end of meaningful work._",
        Path("Welcome.md"): f"# Welcome\n\nStart with {_link(COMMAND_CENTER / 'Home.md', 'the Command Center')}.\n",
    }
    for relative, content in defaults.items():
        path = vault_path(config, relative)
        if not path.exists():
            _write(path, content)
