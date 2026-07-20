"""Opt-in: stage a PDF into a Google Drive folder so it can be dragged into a
NotebookLM notebook. Disabled by default (drive.enabled: false).

Requires the `drive` extra (`uv sync --extra drive`) and OAuth credentials:
- credentials.json  (Desktop OAuth client, from Google Cloud console)
- token.json        (created on first run)
Both live in the repo root and are gitignored.
"""
from __future__ import annotations

from pathlib import Path

from .config import CONFIG, resolve_path

_SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def _service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    token_path = resolve_path("token.json")
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), _SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(resolve_path("credentials.json")), _SCOPES
            )
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())
    return build("drive", "v3", credentials=creds)


def stage_pdf(pdf_path: str | Path, filename: str = "") -> str:
    """Upload the PDF to the configured folder; return the Drive file link.
    `filename` overrides the uploaded name (e.g. a structured Author_Year_topic
    convention) — the local file on disk is untouched either way."""
    from googleapiclient.http import MediaFileUpload

    folder_id = (CONFIG.get("drive") or {}).get("folder_id") or ""
    svc = _service()
    meta = {"name": filename or Path(pdf_path).name}
    if folder_id:
        meta["parents"] = [folder_id]
    media = MediaFileUpload(str(pdf_path), mimetype="application/pdf")
    f = svc.files().create(body=meta, media_body=media, fields="webViewLink").execute()
    return f.get("webViewLink", "")
