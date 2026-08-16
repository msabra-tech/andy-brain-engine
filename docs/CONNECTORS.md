# Google Drive and Notion connectors

Andy Brain reads sources only when Andy or Claude explicitly asks it to. The connector response is temporary: the engine stores only a source identifier, name, link, timestamp, type, and content hash in its local ledger. It does not retain page/document contents.

Both connectors are read-only in this release. The existing approval pipeline remains the only way to create or alter refined Obsidian artifacts. Future external writes will require a separate proposal and Andy's confirmation every time.

## Google Drive

The connector uses a Google Cloud **Desktop app** OAuth client and the `drive.readonly` scope. It lists Andy's user corpus—including files shared with him—and includes Shared Drive support. This is an account-level consent from Andy; no Google credentials are committed or placed in the vault.

1. In the JDS Google Cloud project, enable the Google Drive API.
2. Create an OAuth client for a **Desktop app** and download its JSON file.
3. On Andy's Windows machine, from the engine folder, run:

   ```powershell
   brain connectors google-drive import-client C:\Path\to\google-desktop-client.json
   brain connectors google-drive authorize
   ```

4. A normal system-browser Google consent page opens. Andy signs in to the intended JDS Google Workspace account and grants read-only Drive access.
5. The OAuth client and refresh token are encrypted with Windows DPAPI for Andy's Windows account. They remain under ignored `data/state/secrets/` files, never in the vault or Git.

The authorization flow uses PKCE and a loopback callback, as recommended for desktop applications. It must be completed on the actual Windows machine, not through Claude chat.

## Notion

For a one-person local tool, configure a Notion **Personal Access Token (PAT)** created by Andy. A PAT acts with Andy's Notion permissions, so it can see the pages, databases, files, and other resources Andy can access—subject to Notion workspace policy.

1. Create a PAT in the Notion Developer Portal with the API capability enabled. Use the account Andy intends the tool to represent.
2. On Andy's Windows machine, run:

   ```powershell
   brain connectors notion authorize
   ```

3. Paste the PAT at the hidden prompt. The token is encrypted with Windows DPAPI in ignored local state.

The connector searches currently visible pages and retrieves their enhanced Markdown only for an active Claude request. It calls Notion API version `2026-03-11` and does not read or retain Notion content outside that response.

## Using connectors with Claude

Once configured, ask Claude Desktop to use one of these MCP tools:

- `sync_google_drive` — optionally pass a Drive query or file IDs.
- `sync_notion` — optionally pass a Notion title query or page IDs.
- `connector_status` — confirms configuration without exposing any credential.

Claude should inspect the excerpts, explain the recommended workstream update, and ask Andy before calling the proposal application tools. It cannot silently write to Drive, Notion, or the vault.

## Optional PDF text extraction

Google Drive PDF extraction uses the optional `pypdf` package. If it is not installed, the connector still discovers the file and returns a transparent notice instead of retaining or silently skipping it:

```powershell
py -3 -m pip install pypdf
```
