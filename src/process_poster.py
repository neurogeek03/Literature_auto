"""Orchestrator: poster/slide image -> vision extraction (poster_node) -> note.
Mirrors process_paper.py but for images: no PDF, no DOI/Crossref metadata, no
fulltext or sufficiency gate. The image itself is the content.

CLI (offline, no Slack):
    uv run python -m src.process_poster /path/to/poster.jpg [--prompt "ISMB 26 — poster by Sung lab"]
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import conference, note_render
from .config import CONFIG, images_dir, valid_topics
from .metadata import _ascii_last_name
from .poster_node import run_poster_node

_HEIC_EXTS = {".heic", ".heif"}


@dataclass
class PosterProcResult:
    status: str                       # "ok" | "insufficient" | "error"
    message: str = ""
    note_path: str = ""
    title: str = ""
    authors: list[str] = field(default_factory=list)
    venue: str = ""
    source_type: str = "poster"
    related: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    conference: str = ""
    node_error: str = ""


def _convert_if_heic(path: Path) -> Path:
    """HEIC/HEIF (the default on iPhone) -> JPG via macOS `sips`, so the image
    is both readable by the vision call and portable once embedded in Obsidian."""
    if path.suffix.lower() not in _HEIC_EXTS:
        return path
    out = path.with_suffix(".jpg")
    subprocess.run(
        ["sips", "-s", "format", "jpeg", str(path), "--out", str(out)],
        check=True, capture_output=True,
    )
    return out


def _year_from_conference(slug: str) -> str:
    m = re.search(r"(\d{4})", slug or "")
    return m.group(1) if m else ""


def _short_title_token(title: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", title or "")
    return "".join(w.capitalize() for w in words[:4])[:24] or "Untitled"


def _base_citekey(authors: list[str], year: str, conf_slug: str, title: str) -> str:
    if authors:
        return f"{_ascii_last_name(authors[0])}_{year or 'ND'}"
    return f"Poster_{conf_slug or 'Conf'}{year or 'ND'}_{_short_title_token(title)}"


def process_image(image_path: str | Path, prompt_text: str = "") -> PosterProcResult:
    image_path = Path(image_path)
    if not image_path.exists():
        return PosterProcResult(status="error", message=f"image not found: {image_path}")

    conf_slug, focus_hint = conference.parse_prompt(prompt_text)
    if conf_slug:
        conference.ensure_stub(conf_slug)

    try:
        work_image = _convert_if_heic(image_path)
    except Exception as e:
        return PosterProcResult(status="error", message=f"couldn't convert HEIC image: {e}")

    node = run_poster_node(work_image, valid_topics(), focus_hint=focus_hint)
    if not node.ok:
        return PosterProcResult(
            status="insufficient",
            message=f"Couldn't read that image well enough (`{node.error}`) — "
                    "try a clearer or closer photo.",
        )

    year = _year_from_conference(conf_slug)
    base_citekey = _base_citekey(node.authors, year, conf_slug, node.title)
    target = note_render.poster_target_path(base_citekey)
    citekey = target.stem.lstrip("@")

    # Fixed per-citekey filename (no poster/slide suffix): re-dropping the same
    # author's poster/slide overwrites this same image file too, instead of
    # leaving an orphaned copy behind if source_type happens to differ.
    image_dest = images_dir() / f"{citekey}{work_image.suffix.lower()}"
    try:
        shutil.copy(work_image, image_dest)
    except Exception as e:
        return PosterProcResult(status="error", message=f"couldn't save image into the vault: {e}")

    embed_text = (node.title + "\n" + node.bullets).strip()
    related_keys: list[str] = []
    if embed_text:
        from . import related as related_mod  # lazy: loads fastembed model on demand
        related_keys = related_mod.related(embed_text, exclude_citekey=citekey)

    try:
        path = note_render.write_poster_note(
            citekey=citekey,
            title=node.title,
            authors=node.authors,
            year=year,
            venue=node.venue,
            source_type=node.source_type,
            image_filename=image_dest.name,
            topics=node.topics,
            related_keys=related_keys,
            node_bullets=node.bullets,
            conference=conf_slug,
        )
    except Exception as e:
        return PosterProcResult(status="error", message=f"failed while building the note: {e}")

    return PosterProcResult(
        status="ok",
        message=f"Wrote {path.name}",
        note_path=str(path),
        title=node.title,
        authors=node.authors,
        venue=node.venue,
        source_type=node.source_type,
        related=related_keys,
        topics=node.topics,
        conference=conf_slug,
    )


def _main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Process one poster/slide image -> Obsidian note.")
    ap.add_argument("image")
    ap.add_argument("--prompt", default="", help="caption text (conference tag + focus hint)")
    args = ap.parse_args(argv)

    r = process_image(args.image, prompt_text=args.prompt)
    print(f"status: {r.status}")
    if r.status == "ok":
        print(f"title:   {r.title}")
        print(f"authors: {r.authors}")
        print(f"venue:   {r.venue}")
        print(f"type:    {r.source_type}")
        print(f"note:    {r.note_path}")
        print(f"topics:  {r.topics}")
        print(f"related: {r.related}")
    else:
        print(f"message: {r.message}")
    return 0 if r.status == "ok" else 1


if __name__ == "__main__":
    sys.exit(_main())
