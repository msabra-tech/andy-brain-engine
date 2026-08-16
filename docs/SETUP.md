# Setup

This project is macOS-first. It creates a local engine, an iCloud Drive bridge, and an Obsidian vault inside Obsidian's iCloud container.

## Quick Start

```sh
git clone <repo-url>
cd personal-brain-engine
./scripts/setup.sh
```

The setup script prompts for:

- owner name
- vault title
- Obsidian iCloud vault folder name
- bridge folder name
- timezone
- PLAUD MCP status
- background runner install

## Required Mac State

- iCloud Drive is enabled on the Mac.
- Obsidian is installed.
- Obsidian has been opened once with iCloud Drive enabled.
- The vault must live at the top level of:

`~/Library/Mobile Documents/iCloud~md~obsidian/Documents/`

## What Setup Creates

- `config/paths.local.json`
- `config/runtime.local.json`
- `config/integrations.local.json`
- a clean Obsidian vault from `templates/vault`
- an iCloud bridge with `inbox`, `outbox`, `receipts`, and `errors`
- local engine state folders
- an empty reminder action queue

The script then runs:

```sh
./brain publish
./brain verify
```

If you choose to install the runner, it also runs:

```sh
./brain runner install
```

## Dry Run

```sh
./scripts/setup.sh --dry-run --owner-name "Example Owner" --vault-title "Example Brain"
```

## Transcript Test

Save a `.txt` or `.md` file in:

`~/Library/Mobile Documents/com~apple~CloudDocs/<Bridge Name>/inbox/mobile/`

Then run:

```sh
./brain ingest
./brain publish
./brain verify
```

Raw audio can be placed in `inbox/audio`, but V1 does not transcribe audio by itself. Audio is archived and surfaced as an open question until a transcript is supplied.
