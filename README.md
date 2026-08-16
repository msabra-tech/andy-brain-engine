# Personal Brain Engine

A local-first second-brain engine that maintains a clean Obsidian vault in iCloud Drive.

The repo keeps software, raw archives, runtime state, and integrations outside Obsidian. The Obsidian vault stays human-facing: `Home`, `Today`, `Life`, `Work`, `Career`, `Projects`, `People`, `Ideas`, and `References`.

## Quick Start

```sh
git clone <repo-url>
cd personal-brain-engine
./scripts/setup.sh
```

Setup creates:

- an Obsidian iCloud vault from `templates/vault`
- an iCloud Drive bridge for captures
- local config files
- local engine state folders
- an empty reminder queue
- optional background LaunchAgent runner

The setup script also prompts for PLAUD MCP status. It does not store PLAUD passwords, API keys, or tokens.

## Core Commands

```sh
./brain audit
./brain ingest
./brain publish
./brain review
./brain verify
./brain run-once
./brain status
```

## Runner Commands

```sh
./brain runner install
./brain runner start
./brain runner stop
./brain runner restart
./brain runner status
./brain runner logs
./brain runner run-now
./brain runner uninstall
```

## Reminder Commands

```sh
./brain reminders doctor
./brain reminders plan
./brain reminders apply --dry-run
./brain reminders apply --live
./brain reminders status
./brain reminders retry
./brain reminders rollback-test
```

Markdown alone cannot create an iPhone push notification. Native notifications require Apple Reminders, Calendar, Shortcuts, an authorized MCP integration, or another external notification system.

Automatic live reminders are disabled by default. Enable only after a successful live test and iPhone sync confirmation:

```sh
./brain config set automatic-reminders true
```

## Capture Format

For the cleanest V1 behavior, save `.txt` or `.md` transcript files into the bridge inbox and use clear labels:

```txt
Task: prepare the sample dashboard before dinner
Work: capture one client follow-up as a work note
Career: save one portfolio idea for later
Project: expand the AI Second Brain project map
Person: ask which people notes should be tracked
Idea: make unresolved items visible on the dashboard
Reflection: the system should be honest about uncertain facts
Decision: keep private account connections disabled until authorized
Question: whether PLAUD export access is available
Shopping list: add shampoo and soap.
Remind me at 2026-07-20T19:30:00+03:00 to review the plan.
```

Raw audio can be placed in `inbox/audio`, but V1 does not transcribe audio by itself. Audio is archived and surfaced as an open question until a text transcript is supplied.

## Docs

- [Setup](docs/SETUP.md)
- [PLAUD MCP](docs/PLAUD_MCP.md)
- [GitHub release checklist](docs/GITHUB_RELEASE.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Data boundaries](docs/DATA_BOUNDARIES.md)
