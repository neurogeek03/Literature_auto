"""Vision extraction step for poster/slide image drops: one headless Claude
Code call that reads the image (via the Read tool) and returns title/authors/
venue/type plus key-point bullets and 1-3 topic slugs. Mirrors node.py's
paper-node call, but the image itself is the input instead of pasted text.

`--allowedTools Read` is load-bearing: it pre-authorizes exactly the one tool
call the skill needs (Read) so a headless/unattended run never blocks on a
permission prompt.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .config import CONFIG


@dataclass
class PosterResult:
    title: str = ""
    authors: list[str] = field(default_factory=list)
    venue: str = ""
    source_type: str = "poster"  # "poster" | "slide"
    bullets: str = ""
    topics: list[str] = field(default_factory=list)
    ok: bool = False
    error: str = ""


def _field(text: str, name: str) -> str:
    # [ \t]* (not \s*) around the colon: \s matches newlines too, so on an
    # empty field (e.g. "VENUE:\n") a \s* capture would silently swallow the
    # line break and bleed into the next field's value.
    m = re.search(rf"(?im)^[ \t]*{name}[ \t]*:[ \t]*(.*)$", text)
    return m.group(1).strip() if m else ""


def _parse(stdout: str, valid_topics: set[str]) -> PosterResult:
    text = stdout.strip()

    m = re.match(r"(?is)^\s*ERROR\s*:\s*(.+)", text)
    if m:
        return PosterResult(error=m.group(1).strip()[:300])

    result = PosterResult()

    tm = re.search(r"(?im)^[ \t]*TOPICS?[ \t]*:[ \t]*(.+)$", text)
    if tm:
        for tok in re.split(r"[,\n]", tm.group(1)):
            slug = tok.strip().strip("[]").split("/")[-1].strip().lower()
            slug = re.sub(r"[^a-z0-9-]", "", slug)
            if slug and slug in valid_topics and slug not in result.topics:
                result.topics.append(slug)
        result.topics = result.topics[:3]
        text = text[: tm.start()]  # bullets + fields are everything before TOPICS

    result.title = _field(text, "TITLE")
    result.venue = _field(text, "VENUE")

    authors_raw = _field(text, "AUTHORS")
    if authors_raw and authors_raw.strip().lower() not in {"unknown", "n/a", "none", ""}:
        result.authors = [a.strip() for a in authors_raw.split(";") if a.strip()]

    st = _field(text, "SOURCE_TYPE").strip().lower()
    result.source_type = "slide" if st.startswith("slide") else "poster"

    bullets = []
    for line in text.splitlines():
        s = line.strip()
        if re.match(r"(?i)^(KEYPOINTS?|TITLE|AUTHORS|VENUE|SOURCE_TYPE)\s*:", s):
            continue
        if s.startswith(("-", "*", "•")):
            bullets.append("- " + s.lstrip("-*• ").strip())
    result.bullets = "\n".join(bullets)
    result.ok = bool(result.bullets)
    return result


def run_poster_node(
    image_path: str | Path, valid_topics: set[str], focus_hint: str = ""
) -> PosterResult:
    ncfg = CONFIG["node"]
    skill = ncfg.get("poster_skill", "poster-node")
    focus_line = f"Additional context from the user: {focus_hint}\n\n" if focus_hint else ""
    prompt = (
        f"/{skill}\n\n"
        f"{focus_line}"
        f"Image path: {Path(image_path).resolve()}\n"
    )
    cmd = [
        ncfg["claude_bin"], "-p", "--model", ncfg["model"],
        "--allowedTools", "Read",
    ]
    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=int(ncfg.get("timeout_seconds", 180)),
        )
    except FileNotFoundError:
        return PosterResult(error=f"claude not found at {ncfg['claude_bin']}")
    except subprocess.TimeoutExpired:
        return PosterResult(error="claude timed out")

    if proc.returncode != 0:
        return PosterResult(error=(proc.stderr or "claude returned non-zero").strip()[:300])

    result = _parse(proc.stdout, valid_topics)
    if not result.ok and not result.error:
        result.error = "no bullets parsed from claude output"
    return result
