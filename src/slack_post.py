"""Build and send Slack replies — success cards AND error/prompt messages.
Single place every outcome surfaces, so nothing is a silent no-op."""
from __future__ import annotations

from .process_paper import Result
from .process_poster import PosterProcResult

NOTEBOOKLM_URL = "https://notebooklm.google.com/"


def _obsidian_uri(note_path: str) -> str:
    # Clickable open-in-Obsidian link (works on the Mac).
    from urllib.parse import quote

    return f"obsidian://open?path={quote(note_path)}"


def format_result(result: Result, notebooklm_link: str | None = None) -> str:
    if result.status != "ok":
        # Insufficient / error: the message is already human-facing.
        return result.message

    m = result.meta
    authors = "; ".join(m.authors[:4]) + (" et al." if len(m.authors) > 4 else "")
    lines = [
        f"*{m.title}*",
        f"_{authors}_  ·  {m.year}  ·  {m.venue}".strip(" ·"),
    ]
    if m.doi:
        lines.append(f"<https://doi.org/{m.doi}|doi.org/{m.doi}>")
    if result.topics:
        lines.append("Topics: " + ", ".join(f"`{t}`" for t in result.topics))
    if result.related:
        lines.append("Related: " + ", ".join(f"`@{r}`" for r in result.related))
    if result.code:
        lines.append("Code: " + " ".join(f"<{u}>" for u in result.code))
    if result.node_error:
        lines.append(f"_(node skipped: {result.node_error})_")
    lines.append(f"Note: <{_obsidian_uri(result.note_path)}|open in Obsidian>")
    if notebooklm_link == "STAGING_FAILED":
        lines.append("_(NotebookLM staging failed — check .cache/listener.err.log)_")
    elif notebooklm_link:
        lines.append(f"Podcast: <{notebooklm_link}|stage in NotebookLM>")
    return "\n".join(lines)


def format_poster_result(result: PosterProcResult) -> str:
    if result.status != "ok":
        return result.message

    label = "Poster" if result.source_type == "poster" else "Slide"
    authors = (
        "; ".join(result.authors[:4]) + (" et al." if len(result.authors) > 4 else "")
        if result.authors else "authors not identified"
    )
    lines = [
        f"*{result.title or '(untitled)'}*  _( {label} )_",
        f"_{authors}_" + (f"  ·  {result.venue}" if result.venue else ""),
    ]
    if result.topics:
        lines.append("Topics: " + ", ".join(f"`{t}`" for t in result.topics))
    if result.related:
        lines.append("Related: " + ", ".join(f"`@{r}`" for r in result.related))
    lines.append(f"Note: <{_obsidian_uri(result.note_path)}|open in Obsidian>")
    return "\n".join(lines)


def post_poster_result(
    client, channel: str, result: PosterProcResult, thread_ts: str | None = None
) -> None:
    send(client, channel, format_poster_result(result), thread_ts=thread_ts)


def send(client, channel: str, text: str, thread_ts: str | None = None) -> None:
    client.chat_postMessage(
        channel=channel, text=text, thread_ts=thread_ts, unfurl_links=False
    )


def post_result(
    client,
    channel: str,
    result: Result,
    thread_ts: str | None = None,
    notebooklm_link: str | None = None,
) -> None:
    send(client, channel, format_result(result, notebooklm_link), thread_ts=thread_ts)
