"""Slack Socket Mode listener. Watches the private channel for dropped PDFs
(or DOI/URL messages) and runs the pipeline. The event-handling logic is shared
with catchup.py so papers dropped while the Mac was off get identical treatment.

Run:  uv run python -m src.slack_listener
"""
from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
from pathlib import Path

import requests

from . import drive, metadata, slack_post
from .config import CONFIG, resolve_path
from .process_paper import process_pdf

MARKER = "white_check_mark"  # reaction added to a processed message (UX + dedup)


# ----- state (last-processed timestamp) -----

def _state_path() -> Path:
    return resolve_path(CONFIG["paths"]["state_file"])


def load_state() -> dict:
    p = _state_path()
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {"last_ts": "0"}


def save_last_ts(ts: str) -> None:
    state = load_state()
    if float(ts) > float(state.get("last_ts", "0")):
        state["last_ts"] = ts
        _state_path().write_text(json.dumps(state))


# ----- helpers -----

def _work_dir() -> Path:
    d = resolve_path(CONFIG["paths"]["work_dir"])
    d.mkdir(parents=True, exist_ok=True)
    return d


def _bot_token() -> str:
    return os.environ["SLACK_BOT_TOKEN"]


def download_slack_file(file_obj: dict) -> Path:
    url = file_obj.get("url_private_download") or file_obj["url_private"]
    dest = _work_dir() / (file_obj.get("id", "file") + "_" + file_obj.get("name", "paper.pdf"))
    r = requests.get(url, headers={"Authorization": f"Bearer {_bot_token()}"}, timeout=60)
    r.raise_for_status()
    dest.write_bytes(r.content)
    return dest


_UA = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


def download_url(url: str) -> Path:
    dest = _work_dir() / "url_download.pdf"
    r = requests.get(url, headers=_UA, timeout=60)
    r.raise_for_status()
    if not r.content[:5] == b"%PDF-":
        raise ValueError("that link did not return a PDF (likely a paywall page)")
    dest.write_bytes(r.content)
    return dest


def resolve_pdf_from_doi(doi: str) -> str:
    """Best open-access PDF url for a DOI, or '' if paywalled."""
    oa = metadata.fetch_openalex(doi)
    if oa and oa.oa_pdf_url:
        return oa.oa_pdf_url
    return metadata.fetch_unpaywall_pdf(doi)


def _react(client, channel: str, ts: str) -> None:
    try:
        client.reactions_add(channel=channel, timestamp=ts, name=MARKER)
    except Exception:
        pass


def _stage_notebooklm(pdf_path: Path) -> str | None:
    if not (CONFIG.get("drive") or {}).get("enabled"):
        return None
    try:
        drive.stage_pdf(pdf_path)
        return slack_post.NOTEBOOKLM_URL
    except Exception:
        return None


# ----- the shared handler -----

def handle_event(client, event: dict) -> None:
    """Process one channel message event. Safe to call from listener or catchup."""
    channel = event.get("channel")
    if channel != CONFIG["slack"].get("channel_id"):
        return
    if event.get("bot_id") or event.get("subtype") in {
        "message_changed", "message_deleted", "channel_join", "bot_message",
    }:
        return

    ts = event.get("ts", "0")
    files = event.get("files") or []
    text = event.get("text") or ""

    pdfs = [f for f in files if (f.get("filetype") == "pdf" or f.get("name", "").lower().endswith(".pdf"))]

    try:
        if pdfs:
            for f in pdfs:
                pdf = download_slack_file(f)
                _run_and_reply(client, channel, ts, pdf)
        elif text.strip():
            _handle_text(client, channel, ts, text)
        else:
            return
        _react(client, channel, ts)
    finally:
        save_last_ts(ts)


def _run_and_reply(client, channel: str, ts: str, pdf: Path, doi: str = "") -> None:
    result = process_pdf(pdf, doi=doi)
    nb = _stage_notebooklm(pdf) if result.status == "ok" else None
    slack_post.post_result(client, channel, result, thread_ts=ts, notebooklm_link=nb)


def _handle_text(client, channel: str, ts: str, text: str) -> None:
    url = _first_url(text)

    # 1. Direct PDF link -> download it (DOI is extracted from the file).
    if url and url.lower().endswith(".pdf"):
        try:
            _run_and_reply(client, channel, ts, download_url(url))
        except Exception as e:
            slack_post.send(
                client, channel,
                f"Couldn't download that PDF (`{e}`) — drop the file directly instead.",
                thread_ts=ts,
            )
        return

    # 2. Publisher article page (Nature, Cell, etc.) -> read its DOI + any
    #    citation_pdf_url from the page's meta tags, then resolve an OA PDF.
    doi = ""
    page_pdf = ""
    if url:
        doi, page_pdf = _resolve_landing_page(url)

    # 3. A bare DOI in the message text.
    doi = doi or metadata.find_doi(text)

    if not doi and not page_pdf:
        return  # not a paper reference; ignore quietly.

    # Try the publisher's own PDF link first, then an open-access copy (OpenAlex/
    # Unpaywall). A paywall page masquerading as a PDF fails the magic-byte check
    # in download_url and we move to the next candidate.
    candidates = [page_pdf, resolve_pdf_from_doi(doi) if doi else ""]
    for pdf_url in candidates:
        if not pdf_url:
            continue
        try:
            _run_and_reply(client, channel, ts, download_url(pdf_url), doi=doi)
            return
        except Exception:
            continue

    label = f"`{doi}`" if doi else "that paper"
    slack_post.send(
        client, channel,
        f"Couldn't reach the full text for {label} — grab it with your "
        "credentials and drop the PDF here.",
        thread_ts=ts,
    )


def _first_url(text: str) -> str:
    import re

    m = re.search(r"https?://[^\s|>]+", text)
    return m.group(0).rstrip(">|") if m else ""


def _resolve_landing_page(url: str) -> tuple[str, str]:
    """Fetch a publisher article page and pull (DOI, direct-PDF-url) from its
    citation_* meta tags. Returns ('', '') if the page isn't an article page.
    Nearly every publisher embeds <meta name="citation_doi"> and often
    <meta name="citation_pdf_url">."""
    import re

    try:
        r = requests.get(url, headers=_UA, timeout=30)
        r.raise_for_status()
        html = r.text
    except Exception:
        return "", ""

    def _meta(name: str) -> str:
        for pat in (
            rf'<meta[^>]+name=["\']{name}["\'][^>]+content=["\']([^"\']+)',
            rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']{name}["\']',
        ):
            m = re.search(pat, html, re.IGNORECASE)
            if m:
                return m.group(1).strip()
        return ""

    return _meta("citation_doi"), _meta("citation_pdf_url")


# ----- network readiness -----

def _wait_for_network(host: str = "slack.com", port: int = 443, timeout: int = 120) -> None:
    """Block until DNS resolves host, retrying every 5 s. Prevents the flood of
    'nodename nor servname provided' errors that slack_bolt emits when launchd
    starts this process before the network is up (boot or wake-from-sleep)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            socket.getaddrinfo(host, port)
            return
        except socket.gaierror:
            logging.warning("Network not ready, retrying in 5 s…")
            time.sleep(5)
    logging.error("Network did not become available after %d s — proceeding anyway.", timeout)


# ----- entrypoint -----

def run() -> None:
    from slack_bolt import App
    from slack_bolt.adapter.socket_mode import SocketModeHandler

    from . import catchup

    _wait_for_network()
    app = App(token=_bot_token())

    @app.event("message")
    def on_message(event, client):
        threading.Thread(target=handle_event, args=(client, event), daemon=True).start()

    # Reconcile anything dropped while we were offline, then go live.
    catchup.run_catchup(app.client)
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()


if __name__ == "__main__":
    run()
