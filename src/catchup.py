"""Reconcile papers dropped while the Mac was off.

Slack is the durable queue: on startup (and callable on a timer) we read channel
history since the last-processed timestamp and run anything we missed through the
same handler the live listener uses.
"""
from __future__ import annotations

from .config import CONFIG
from . import slack_listener as sl


def run_catchup(client) -> int:
    channel = CONFIG["slack"].get("channel_id")
    if not channel:
        return 0
    last_ts = sl.load_state().get("last_ts", "0")

    processed = 0
    cursor = None
    pending: list[dict] = []
    # Page through everything newer than last_ts.
    while True:
        resp = client.conversations_history(
            channel=channel, oldest=last_ts, limit=200, cursor=cursor
        )
        pending.extend(resp.get("messages", []))
        cursor = (resp.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            break

    # Oldest first, skip the boundary message we already handled.
    for msg in sorted(pending, key=lambda m: float(m.get("ts", "0"))):
        if msg.get("ts", "0") == last_ts:
            continue
        # Reaction marker means we already processed it.
        if any(r.get("name") == sl.MARKER for r in (msg.get("reactions") or [])):
            continue
        msg = {**msg, "channel": channel}
        sl.handle_event(client, msg)
        processed += 1
    return processed
