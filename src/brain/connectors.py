"""Read-only Google Drive and Notion connectors with ephemeral source handling."""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import io
import json
import secrets
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable

from .config import Config
from .secrets import SecretStoreError, get_secret, secret_configured, store_secret
from .state import read_json, write_json


SOURCES_PATH = Path("config/sources.local.json")
LEDGER_PATH = Path("data/state/source-ledger.json")
GOOGLE_CLIENT_SECRET = "google_drive_client"
GOOGLE_TOKEN_SECRET = "google_drive_tokens"
NOTION_TOKEN_SECRET = "notion_token"
# Andy explicitly chose account-level access. The engine, not OAuth alone, enforces
# the important boundary: every write must originate from an approved proposal.
GOOGLE_SCOPE = "https://www.googleapis.com/auth/drive"
NOTION_VERSION = "2026-03-11"

JsonRequest = Callable[[str, str, dict[str, str], dict[str, Any] | None], dict[str, Any]]
BytesRequest = Callable[[str, str, dict[str, str]], bytes]


def _source_config(config: Config) -> dict[str, Any]:
    return read_json(config.engine / SOURCES_PATH, {"version": 1, "local_folders": [], "connectors": {}})


def _save_source_config(config: Config, source_config: dict[str, Any]) -> None:
    write_json(config.engine / SOURCES_PATH, source_config)


def _connector_settings(config: Config, name: str) -> dict[str, Any]:
    settings = _source_config(config)
    connectors = settings.setdefault("connectors", {})
    return connectors.setdefault(name, {"status": "not_connected", "mode": "approval_required"})


def _set_connector_settings(config: Config, name: str, updates: dict[str, Any]) -> None:
    settings = _source_config(config)
    connector = settings.setdefault("connectors", {}).setdefault(name, {"status": "not_connected", "mode": "approval_required"})
    connector.update(updates)
    _save_source_config(config, settings)


def _ledger(config: Config) -> dict[str, Any]:
    return read_json(config.engine / LEDGER_PATH, {"version": 1, "sources": {}})


def _write_ledger(config: Config, ledger: dict[str, Any]) -> None:
    write_json(config.engine / LEDGER_PATH, ledger)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _request_json(method: str, url: str, headers: dict[str, str], payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    full_headers = {"Accept": "application/json", **headers}
    if data is not None:
        full_headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(url, data=data, headers=full_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:800]
        raise RuntimeError(f"connector request failed ({exc.code}): {body}") from exc


def _request_bytes(method: str, url: str, headers: dict[str, str]) -> bytes:
    request = urllib.request.Request(url, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:800]
        raise RuntimeError(f"connector download failed ({exc.code}): {body}") from exc


def _request_json_bytes(method: str, url: str, headers: dict[str, str], body: bytes) -> dict[str, Any]:
    request = urllib.request.Request(url, data=body, headers={"Accept": "application/json", **headers}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")[:800]
        raise RuntimeError(f"connector write failed ({exc.code}): {error_body}") from exc


def _public_connector_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Defensively prevent a mistaken local config value from becoming MCP output."""
    sensitive_markers = ("token", "secret", "password", "authorization", "credential")
    return {key: value for key, value in settings.items() if not any(marker in key.lower() for marker in sensitive_markers)}


def connector_status(config: Config) -> dict[str, Any]:
    settings = _source_config(config)
    connectors = settings.get("connectors", {})
    google = _public_connector_settings(dict(connectors.get("google_drive", {})))
    notion = _public_connector_settings(dict(connectors.get("notion", {})))
    google["credential_configured"] = secret_configured(config, GOOGLE_CLIENT_SECRET)
    google["token_configured"] = secret_configured(config, GOOGLE_TOKEN_SECRET)
    google["mode"] = "approval_required"
    notion["token_configured"] = secret_configured(config, NOTION_TOKEN_SECRET)
    notion["mode"] = "approval_required"
    return {
        "local_folders": settings.get("local_folders", []),
        "connectors": {"google_drive": google, "notion": notion},
        "note": "Credentials are protected for Andy's Windows account or supplied only through process environment variables. They are never returned by this tool.",
    }


def import_google_drive_client(config: Config, client_file: str) -> dict[str, Any]:
    """Import a Desktop OAuth client file into Windows DPAPI storage."""
    payload = json.loads(Path(client_file).expanduser().read_text(encoding="utf-8"))
    client = payload.get("installed") or payload.get("web") or payload
    client_id = str(client.get("client_id", "")).strip()
    if not client_id:
        raise ValueError("Google OAuth client file does not contain client_id")
    store_secret(config, GOOGLE_CLIENT_SECRET, json.dumps(client))
    _set_connector_settings(
        config,
        "google_drive",
        {
            "status": "client_configured",
            "mode": "approval_required",
            "scope": GOOGLE_SCOPE,
            "configured_at": _now(),
        },
    )
    return {"configured": True, "connector": "google_drive", "mode": "approval_required", "next_step": "Run `brain connectors google-drive authorize` on Andy's Windows machine."}


def _google_client(config: Config) -> dict[str, Any]:
    try:
        client = json.loads(get_secret(config, GOOGLE_CLIENT_SECRET))
    except (json.JSONDecodeError, SecretStoreError) as exc:
        raise RuntimeError("Google Drive is not configured. Import a Desktop OAuth client first.") from exc
    if not client.get("client_id"):
        raise RuntimeError("Google OAuth client configuration is missing client_id")
    return client


def _code_verifier() -> str:
    return secrets.token_urlsafe(64)[:96]


def _code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _token_request(url: str, fields: dict[str, str]) -> dict[str, Any]:
    encoded = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(url, data=encoded, headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:800]
        raise RuntimeError(f"Google OAuth token exchange failed ({exc.code}): {body}") from exc


class _OAuthCallback(HTTPServer):
    def __init__(self, server_address: tuple[str, int], request_handler: type[BaseHTTPRequestHandler]):
        super().__init__(server_address, request_handler)
        self.authorization_code: str | None = None
        self.authorization_error: str | None = None
        self.expected_state = ""
        self.received = threading.Event()


class _OAuthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - HTTP method name is defined by BaseHTTPRequestHandler.
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        server = self.server
        if not isinstance(server, _OAuthCallback):
            self.send_error(500)
            return
        if query.get("state", [""])[0] != server.expected_state:
            server.authorization_error = "OAuth state did not match"
        elif query.get("error"):
            server.authorization_error = query["error"][0]
        else:
            server.authorization_code = query.get("code", [None])[0]
        server.received.set()
        body = b"<html><body><h2>Andy Brain is connected.</h2><p>You can close this window and return to the terminal.</p></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003 - silence token-bearing callback URLs in console logs.
        return


def authorize_google_drive(config: Config, timeout_seconds: int = 300) -> dict[str, Any]:
    """Open a consent browser and store only the encrypted OAuth token on Windows."""
    client = _google_client(config)
    callback = _OAuthCallback(("127.0.0.1", 0), _OAuthHandler)
    verifier = _code_verifier()
    state = secrets.token_urlsafe(24)
    callback.expected_state = state
    redirect_uri = f"http://127.0.0.1:{callback.server_port}/oauth/google-drive"
    query = {
        "client_id": client["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": GOOGLE_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "code_challenge": _code_challenge(verifier),
        "code_challenge_method": "S256",
    }
    authorization_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(query)
    webbrowser.open(authorization_url)
    callback.timeout = 1
    deadline = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=timeout_seconds)
    try:
        while not callback.received.is_set() and dt.datetime.now(dt.timezone.utc) < deadline:
            callback.handle_request()
    finally:
        callback.server_close()
    if callback.authorization_error:
        raise RuntimeError(f"Google Drive authorization was not completed: {callback.authorization_error}")
    if not callback.authorization_code:
        raise RuntimeError("Google Drive authorization timed out before Google returned a code")
    fields = {
        "code": callback.authorization_code,
        "client_id": client["client_id"],
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
        "code_verifier": verifier,
    }
    if client.get("client_secret"):
        fields["client_secret"] = str(client["client_secret"])
    tokens = _token_request("https://oauth2.googleapis.com/token", fields)
    if not tokens.get("refresh_token"):
        raise RuntimeError("Google did not return a refresh token. Re-run authorization and approve access.")
    tokens["obtained_at"] = _now()
    store_secret(config, GOOGLE_TOKEN_SECRET, json.dumps(tokens))
    _set_connector_settings(config, "google_drive", {"status": "connected", "mode": "approval_required", "scope": GOOGLE_SCOPE, "connected_at": _now()})
    return {"connected": True, "connector": "google_drive", "mode": "approval_required", "scope": GOOGLE_SCOPE}


def _google_access_token(config: Config) -> str:
    client = _google_client(config)
    try:
        tokens = json.loads(get_secret(config, GOOGLE_TOKEN_SECRET))
    except (json.JSONDecodeError, SecretStoreError) as exc:
        raise RuntimeError("Google Drive is not authorized. Run `brain connectors google-drive authorize` on Andy's Windows machine.") from exc
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        raise RuntimeError("Google Drive authorization does not contain a refresh token")
    fields = {"client_id": client["client_id"], "refresh_token": refresh_token, "grant_type": "refresh_token"}
    if client.get("client_secret"):
        fields["client_secret"] = str(client["client_secret"])
    refreshed = _token_request("https://oauth2.googleapis.com/token", fields)
    access_token = refreshed.get("access_token")
    if not access_token:
        raise RuntimeError("Google Drive refresh did not return an access token")
    tokens.update({key: value for key, value in refreshed.items() if key != "refresh_token"})
    tokens["refreshed_at"] = _now()
    store_secret(config, GOOGLE_TOKEN_SECRET, json.dumps(tokens))
    return str(access_token)


def _drive_text(file: dict[str, Any], headers: dict[str, str], request_bytes: BytesRequest) -> str:
    file_id = str(file["id"])
    mime_type = str(file.get("mimeType", ""))
    if mime_type == "application/vnd.google-apps.folder":
        return "[folder metadata only]"
    if mime_type.startswith("application/vnd.google-apps"):
        url = f"https://www.googleapis.com/drive/v3/files/{urllib.parse.quote(file_id, safe='')}/export?mimeType=text%2Fplain"
    else:
        url = f"https://www.googleapis.com/drive/v3/files/{urllib.parse.quote(file_id, safe='')}?alt=media"
    content = request_bytes("GET", url, headers)
    if mime_type == "application/pdf":
        try:
            from pypdf import PdfReader  # type: ignore[import-not-found]
        except ImportError:
            return "[PDF discovered. Install optional pypdf support to extract text for Claude.]"
        try:
            return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(content)).pages)
        except Exception as exc:
            return f"[PDF text extraction failed: {exc}]"
    if mime_type.startswith("text/") or mime_type in {"application/json", "application/xml", "application/rtf", ""} or mime_type.startswith("application/vnd.google-apps"):
        return content.decode("utf-8", errors="replace")
    return f"[{mime_type or 'binary'} file discovered; text extraction is not configured]"


def sync_google_drive(
    config: Config,
    *,
    file_ids: list[str] | None = None,
    query: str | None = None,
    max_items: int = 20,
    excerpt_limit: int = 6000,
    request_json: JsonRequest = _request_json,
    request_bytes: BytesRequest = _request_bytes,
) -> dict[str, Any]:
    """Read Drive content only into this invocation; store metadata and hashes only."""
    access_token = _google_access_token(config)
    headers = {"Authorization": f"Bearer {access_token}"}
    fields = "nextPageToken,incompleteSearch,files(id,name,mimeType,modifiedTime,webViewLink,description,md5Checksum,size)"
    files: list[dict[str, Any]] = []
    if file_ids:
        for file_id in file_ids[:max_items]:
            url = f"https://www.googleapis.com/drive/v3/files/{urllib.parse.quote(file_id, safe='')}?fields={urllib.parse.quote('id,name,mimeType,modifiedTime,webViewLink,description,md5Checksum,size', safe=',')}"
            files.append(request_json("GET", url, headers))
    else:
        params = {"pageSize": str(min(max(max_items, 1), 100)), "orderBy": "modifiedTime desc", "spaces": "drive", "corpora": "user", "includeItemsFromAllDrives": "true", "supportsAllDrives": "true", "fields": fields}
        if query:
            params["q"] = query
        response = request_json("GET", "https://www.googleapis.com/drive/v3/files?" + urllib.parse.urlencode(params), headers)
        files = list(response.get("files", []))[:max_items]
    ledger = _ledger(config)
    records: list[dict[str, Any]] = []
    for file in files:
        file_id = str(file.get("id", ""))
        if not file_id:
            continue
        try:
            excerpt = _drive_text(file, headers, request_bytes)[:excerpt_limit]
        except RuntimeError as exc:
            excerpt = f"[Unable to retrieve this file's text for the current review: {exc}]"
        digest = hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
        source_id = f"google-drive-{file_id}"
        locator = str(file.get("webViewLink") or f"https://drive.google.com/open?id={file_id}")
        ledger.setdefault("sources", {})[source_id] = {
            "connector": "google_drive",
            "locator": locator,
            "name": file.get("name") or file_id,
            "mime_type": file.get("mimeType"),
            "modified_at": file.get("modifiedTime"),
            "last_seen_at": _now(),
            "sha256": digest,
        }
        records.append({"source_id": source_id, "name": file.get("name") or file_id, "locator": locator, "modified_at": file.get("modifiedTime"), "excerpt": excerpt})
    _write_ledger(config, ledger)
    return {"records": records, "temporary": True, "connector": "google_drive", "retention": "No Google Drive source body was stored. Excerpts exist only in this tool response.", "query": query}


def write_google_drive_text(config: Config, *, title: str, content: str, folder_id: str = "") -> dict[str, Any]:
    """Upload a text artifact after an external-write proposal has been confirmed."""
    access_token = _google_access_token(config)
    metadata: dict[str, Any] = {"name": title, "mimeType": "text/markdown"}
    if folder_id.strip():
        metadata["parents"] = [folder_id.strip()]
    boundary = "andy-brain-" + secrets.token_hex(16)
    body = (
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode("utf-8")
        + json.dumps(metadata).encode("utf-8")
        + f"\r\n--{boundary}\r\nContent-Type: text/markdown; charset=UTF-8\r\n\r\n".encode("utf-8")
        + content.encode("utf-8")
        + f"\r\n--{boundary}--\r\n".encode("utf-8")
    )
    response = _request_json_bytes(
        "POST",
        "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id,name,mimeType,webViewLink",
        {"Authorization": f"Bearer {access_token}", "Content-Type": f"multipart/related; boundary={boundary}", "Content-Length": str(len(body))},
        body,
    )
    return {
        "connector": "google_drive",
        "action": "upload_text",
        "id": response.get("id"),
        "name": response.get("name", title),
        "url": response.get("webViewLink") or (f"https://drive.google.com/open?id={response['id']}" if response.get("id") else None),
    }


def configure_notion_token(config: Config, token: str) -> dict[str, Any]:
    """Store a Notion Personal Access Token encrypted for Andy's Windows account."""
    token = token.strip()
    if not token:
        raise ValueError("Notion token cannot be empty")
    store_secret(config, NOTION_TOKEN_SECRET, token)
    _set_connector_settings(config, "notion", {"status": "connected", "mode": "approval_required", "api_version": NOTION_VERSION, "connected_at": _now(), "authentication": "personal_access_token"})
    return {"configured": True, "connector": "notion", "mode": "approval_required", "authentication": "personal_access_token"}


def _notion_headers(config: Config) -> dict[str, str]:
    try:
        token = get_secret(config, NOTION_TOKEN_SECRET)
    except SecretStoreError as exc:
        raise RuntimeError("Notion is not configured. Run `brain connectors notion authorize` on Andy's Windows machine.") from exc
    return {"Authorization": f"Bearer {token}", "Notion-Version": NOTION_VERSION, "Content-Type": "application/json"}


def _notion_title(page: dict[str, Any]) -> str:
    properties = page.get("properties", {})
    for property_data in properties.values():
        if property_data.get("type") == "title":
            parts = property_data.get("title", [])
            title = "".join(str(part.get("plain_text", "")) for part in parts).strip()
            if title:
                return title
    return str(page.get("url") or page.get("id") or "Untitled Notion page")


def sync_notion(
    config: Config,
    *,
    page_ids: list[str] | None = None,
    query: str | None = None,
    max_items: int = 20,
    excerpt_limit: int = 6000,
    request_json: JsonRequest = _request_json,
) -> dict[str, Any]:
    """Search and retrieve enhanced Notion Markdown only for this request."""
    headers = _notion_headers(config)
    pages: list[dict[str, Any]] = []
    if page_ids:
        for page_id in page_ids[:max_items]:
            pages.append(request_json("GET", f"https://api.notion.com/v1/pages/{urllib.parse.quote(page_id, safe='')}", headers))
    else:
        payload: dict[str, Any] = {"page_size": min(max(max_items, 1), 100), "filter": {"property": "object", "value": "page"}, "sort": {"direction": "descending", "timestamp": "last_edited_time"}}
        if query:
            payload["query"] = query
        response = request_json("POST", "https://api.notion.com/v1/search", headers, payload)
        pages = list(response.get("results", []))[:max_items]
    ledger = _ledger(config)
    records: list[dict[str, Any]] = []
    for page in pages:
        page_id = str(page.get("id", ""))
        if not page_id:
            continue
        try:
            markdown = request_json("GET", f"https://api.notion.com/v1/pages/{urllib.parse.quote(page_id, safe='')}/markdown", headers).get("markdown", "")
            excerpt = str(markdown)[:excerpt_limit]
        except RuntimeError as exc:
            excerpt = f"[Unable to retrieve this page's Markdown for the current review: {exc}]"
        digest = hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
        source_id = f"notion-{page_id}"
        locator = str(page.get("url") or f"https://www.notion.so/{page_id.replace('-', '')}")
        ledger.setdefault("sources", {})[source_id] = {
            "connector": "notion",
            "locator": locator,
            "name": _notion_title(page),
            "modified_at": page.get("last_edited_time"),
            "last_seen_at": _now(),
            "sha256": digest,
        }
        records.append({"source_id": source_id, "name": _notion_title(page), "locator": locator, "modified_at": page.get("last_edited_time"), "excerpt": excerpt})
    _write_ledger(config, ledger)
    return {"records": records, "temporary": True, "connector": "notion", "retention": "No Notion source body was stored. Excerpts exist only in this tool response.", "query": query}


def write_notion_markdown(config: Config, *, title: str, content: str, parent_page_id: str = "") -> dict[str, Any]:
    """Create a Notion page after an external-write proposal has been confirmed."""
    markdown = content.strip()
    if not markdown.startswith("# "):
        markdown = f"# {title.strip()}\n\n{markdown}"
    parent: dict[str, Any] = {"page_id": parent_page_id.strip()} if parent_page_id.strip() else {"workspace": True}
    response = _request_json("POST", "https://api.notion.com/v1/pages", _notion_headers(config), {"parent": parent, "markdown": markdown})
    return {"connector": "notion", "action": "create_page", "id": response.get("id"), "name": title, "url": response.get("url")}


def _local_output_folder(config: Config) -> Path:
    value = _source_config(config).get("local_output_folder", "")
    if not value:
        raise RuntimeError("No local output folder is configured. Run the Windows setup wizard first.")
    path = Path(value).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_local_text(config: Config, *, relative_path: str, content: str) -> dict[str, Any]:
    """Save an approved generated artifact under the configured local output folder."""
    root = _local_output_folder(config)
    target = Path(relative_path)
    if target.is_absolute() or ".." in target.parts or not target.name:
        raise ValueError("local output path must be a relative file path inside Andy Brain's output folder")
    destination = (root / target).resolve()
    try:
        destination.relative_to(root)
    except ValueError as exc:
        raise ValueError("local output path escapes Andy Brain's output folder") from exc
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8")
    return {"connector": "local_file", "action": "save_text", "path": str(destination), "name": destination.name}


def write_external_artifact(config: Config, payload: dict[str, Any]) -> dict[str, Any]:
    """Dispatch a confirmed, synthesized external-write proposal to its connector."""
    connector = payload.get("connector")
    if connector == "google_drive":
        return write_google_drive_text(config, title=payload["title"], content=payload["content"], folder_id=payload.get("target", ""))
    if connector == "notion":
        return write_notion_markdown(config, title=payload["title"], content=payload["content"], parent_page_id=payload.get("target", ""))
    if connector == "local_file":
        return write_local_text(config, relative_path=payload.get("target") or payload["title"], content=payload["content"])
    raise ValueError(f"unsupported external connector: {connector}")
