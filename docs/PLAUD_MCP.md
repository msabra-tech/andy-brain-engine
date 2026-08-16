# PLAUD MCP

PLAUD is intentionally treated as an authorized integration, not as a bundled dependency.

The setup script immediately asks whether a PLAUD MCP server is already installed or ready to connect. It stores only non-secret status in local config:

- `pending_user_authorization`
- `configured_pending_validation`

Do not store PLAUD passwords, API keys, tokens, or private account credentials in this repo, the vault, or the bridge.

Current V1 behavior:

- `.txt`, `.md`, and `.json` transcript exports can be ingested from the bridge.
- raw audio is archived and surfaced for review.
- automated PLAUD import requires an actual authorized PLAUD MCP server or export workflow.

Recommended first validation:

1. Connect PLAUD outside this repo using the owner's approved MCP flow.
2. Export one transcript as text.
3. Place it in the bridge inbox.
4. Run `./brain ingest`.
5. Confirm the resulting notes link back to the generated source card.
