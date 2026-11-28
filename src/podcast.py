"""Make a two-host podcast of a paper note, post it to its Slack thread and
upload it to Spotify for Creators. Runs as its own process (spawned by the
listener) so a listener restart can't kill a long job.

Run:  uv run python -m src.podcast <note.md> [--focus "..."] [--minutes 25]
      [--format deep_dive|critique|debate] [--channel C --thread TS --reply-ts TS]
      [--no-upload] [--reuse-script]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
from pathlib import Path

from .config import CONFIG, resolve_path

FORMATS = ("deep_dive", "critique", "debate")


# ----- Slack command: "podcast [short|long|N min] [critique|debate] [focus...]" -----

def parse_command(text: str) -> dict | None:
    """None if the message isn't a podcast request."""
    m = re.match(r"\s*podcast\b[\s:,-]*(.*)", text, re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    rest = m.group(1).strip()
    opts = {"minutes": int((CONFIG.get("podcast") or {}).get("minutes", 25)), "format": "deep_dive", "focus": ""}
    while rest:
        word = rest.split()[0].lower().strip(",.")
        mins = re.match(r"(\d+)\s*(?:min|mins|minutes)\b[\s:,-]*", rest, re.IGNORECASE)
        if mins:
            opts["minutes"] = max(3, min(60, int(mins.group(1))))
            rest = rest[mins.end():]
        elif word in ("short", "long"):
            opts["minutes"] = 12 if word == "short" else 40
            rest = rest.split(None, 1)[1] if " " in rest else ""
        elif word in ("critique", "debate"):
            opts["format"] = word
            rest = rest.split(None, 1)[1] if " " in rest else ""
        else:
            break
    opts["focus"] = rest.strip()
    return opts


# ----- read the paper note -----

def read_note(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")

    def field(pat: str) -> str:
        m = re.search(pat, text, re.MULTILINE)
        return m.group(1).strip() if m else ""

    full = re.search(r"<summary>Full text.*?</summary>\s*(.*)</details>", text, re.DOTALL)
    return {
        "citekey": field(r"^citekey:\s*(.+)$") or path.stem.lstrip("@"),
        "doi": field(r"^doi:\s*(.+)$"),
        "year": field(r"^year:\s*(.+)$"),
        "title": field(r"\*\*Title\*\*::\s*(.+)$"),
        "authors": field(r"\*\*Authors\*\*::\s*(.+)$"),
        "fulltext": full.group(1).strip() if full else "",
    }


def episode_description(paper: dict) -> str:
    authors = paper["authors"].split(";")
    who = authors[0].strip() + (" et al." if len(authors) > 1 else "")
    desc = f"An AI-generated discussion of \"{paper['title']}\" ({who}, {paper['year']})."
    if paper["doi"]:
        desc += f" Read the paper: https://doi.org/{paper['doi']}"
    return desc


# ----- main job -----

def make_podcast(note: Path, opts: dict, reuse_script: bool = False) -> tuple[Path, dict]:
    from .podcast_audio import render_mp3
    from .podcast_script import write_script

    paper = read_note(note)
    if len(paper["fulltext"].split()) < 500:
        raise RuntimeError("this note has no full text to work from")

    out_dir = resolve_path(CONFIG["podcast"].get("work_dir", ".cache/podcasts"))
    out_dir.mkdir(parents=True, exist_ok=True)
    script_path = out_dir / f"{paper['citekey']}.json"
    if reuse_script and script_path.exists():
        script = json.loads(script_path.read_text(encoding="utf-8"))
    else:
        script = write_script(paper["fulltext"], paper["title"], opts["minutes"], opts["format"], opts["focus"])
        script_path.write_text(json.dumps(script, indent=1), encoding="utf-8")

    mp3 = out_dir / f"{paper['citekey']}.mp3"
    secs = render_mp3(script, mp3)
    logging.info("rendered %s (%.1f min)", mp3, secs / 60)
    return mp3, paper


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("note")
    ap.add_argument("--focus", default="")
    ap.add_argument("--minutes", type=int, default=int((CONFIG.get("podcast") or {}).get("minutes", 25)))
    ap.add_argument("--format", choices=FORMATS, default="deep_dive")
    ap.add_argument("--channel")
    ap.add_argument("--thread")
    ap.add_argument("--reply-ts")
    ap.add_argument("--no-upload", action="store_true")
    ap.add_argument("--reuse-script", action="store_true")
    a = ap.parse_args()

    client = None
    if a.channel and a.thread:
        from slack_sdk import WebClient
        client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])

    def say(msg: str) -> None:
        logging.info(msg)
        if client:
            from . import slack_post
            slack_post.send(client, a.channel, msg, thread_ts=a.thread)

    opts = {"minutes": a.minutes, "format": a.format, "focus": a.focus}
    try:
        mp3, paper = make_podcast(Path(a.note), opts, a.reuse_script)
    except Exception as e:
        logging.exception("podcast failed")
        say(f"Podcast failed (`{type(e).__name__}: {e}`). Reply `podcast` again to retry.")
        return

    if a.no_upload:
        logging.info("done (no upload): %s", mp3)
        return

    delivered = True
    if client:
        from . import slack_post
        try:
            slack_post.upload_file(client, a.channel, mp3, title=paper["title"], thread_ts=a.thread)
        except Exception as e:
            delivered = False
            say(f"Made the podcast but couldn't post it here (`{e}`). It's at `{mp3}`.")

    sp = CONFIG["podcast"].get("spotify") or {}
    if sp.get("enabled"):
        from .spotify_upload import upload
        try:
            how = upload(mp3, paper["title"], episode_description(paper))
            if how == "already there":
                say("This episode is already on Spotify, so I didn't upload it again.")
            else:
                say(f"Published on Spotify for Creators (via {how}).")
        except Exception as e:
            delivered = False
            say(f"Spotify upload failed (`{e}`). The MP3 is above; keeping a local copy.")

    if delivered:
        mp3.unlink(missing_ok=True)
        if client and a.reply_ts:
            try:
                client.reactions_add(channel=a.channel, timestamp=a.reply_ts, name="white_check_mark")
            except Exception:
                pass


if __name__ == "__main__":
    main()
