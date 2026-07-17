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
import sys
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

    if not pdfs and not text.strip():
        save_last_ts(ts)  # empty/system message: nothing to do, safe to skip past.
        return

    try:
        if pdfs:
            for f in pdfs:
                pdf = download_slack_file(f)
                _run_and_reply(client, channel, ts, pdf)
        else:
            _handle_text(client, channel, ts, text)
    except Exception:
        # Fail safe: leave the timestamp and the reaction untouched so catchup
        # re-attempts this message. Advancing state on failure silently drops work.
        logging.exception("Failed to process message ts=%s; leaving it for catchup to retry.", ts)
        return

    # Only mark done (reaction + state) once we've genuinely handled it — including
    # the branches that reply asking for the PDF, which count as a response.
    _react(client, channel, ts)
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

    # 3. A bare DOI in the message text or embedded in the URL itself.
    doi = doi or metadata.find_doi(text) or metadata.find_doi(url)

    if not doi and not page_pdf:
        if url:
            # A link was shared but we couldn't extract a DOI or PDF from it —
            # typically a Cloudflare/anti-bot publisher page (e.g. OUP, Wiley).
            # Never silently drop it: tell the user so they can drop the PDF.
            slack_post.send(
                client, channel,
                "Couldn't read that page (the publisher blocked automated access "
                "and no DOI was in the link) — open it with your credentials and "
                "drop the PDF here.",
                thread_ts=ts,
            )
        return  # no URL at all: ordinary chatter, ignore quietly.

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
    """Block until a full TLS handshake to host succeeds, retrying every 5 s.
    DNS-only checks pass too early on wake-from-sleep; the SSL handshake catches
    captive-portal interception and other half-up network states."""
    import ssl

    ctx = ssl.create_default_context()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=5) as raw:
                with ctx.wrap_socket(raw, server_hostname=host):
                    return  # full TLS handshake succeeded
        except Exception:
            logging.warning("Network not ready (TLS), retrying in 5 s…")
            time.sleep(5)
    logging.error("Network did not become available after %d s — proceeding anyway.", timeout)


# ----- self-healing watchdog -----

_last_healthy = time.monotonic()


def _record_healthy() -> None:
    global _last_healthy
    _last_healthy = time.monotonic()


def _start_watchdog(client, ping_interval: int = 60, max_gap: int = 300) -> None:
    """Two daemon threads:
    • pinger  — calls auth_test() every ping_interval s; updates _last_healthy on success.
    • watchdog — exits the process if _last_healthy is stale for > max_gap s.
    launchd (KeepAlive=true) immediately restarts the process, which then runs
    _wait_for_network() and reconnects cleanly once the network is actually ready.
    """
    def _pinger():
        while True:
            time.sleep(ping_interval)
            try:
                client.auth_test()
                _record_healthy()
            except Exception:
                pass

    def _watchdog():
        time.sleep(max_gap)  # give the process time to connect on first start
        while True:
            if time.monotonic() - _last_healthy > max_gap:
                logging.error(
                    "Slack connection unhealthy for >%ds — exiting so launchd can restart cleanly.",
                    max_gap,
                )
                os._exit(1)  # sys.exit() from a daemon thread doesn't kill the process
            time.sleep(30)

    threading.Thread(target=_pinger, daemon=True, name="slack-pinger").start()
    threading.Thread(target=_watchdog, daemon=True, name="slack-watchdog").start()


# ----- entrypoint -----

def run() -> None:
    from slack_bolt import App
    from slack_bolt.adapter.socket_mode import SocketModeHandler

    from . import catchup

    _wait_for_network()
    _record_healthy()
    app = App(token=_bot_token())

    @app.event("message")
    def on_message(event, client):
        _record_healthy()
        threading.Thread(target=handle_event, args=(client, event), daemon=True).start()

    catchup.run_catchup(app.client)
    _record_healthy()
    _start_watchdog(app.client)
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()


if __name__ == "__main__":
    run()
