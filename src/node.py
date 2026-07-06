"""The one non-deterministic step: a headless Claude Code call that runs the
`paper-node` skill over the full text and returns (a) key-point bullets and
(b) 1-3 topic slugs from the fixed vocabulary. One call, two outputs.

Uses the user's Claude Code subscription (`claude -p`), not the API.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

from .config import CONFIG

# Cap what we pipe in so a giant supplement doesn't blow past context / argv.
_MAX_CHARS = 60_000


@dataclass
class NodeResult:
    bullets: str = ""          # markdown, one "- ..." per line
    topics: list[str] = None   # validated topic slugs
    ok: bool = False
    error: str = ""

    def __post_init__(self):
        if self.topics is None:
            self.topics = []


def _parse(stdout: str, valid_topics: set[str]) -> tuple[str, list[str]]:
    text = stdout.strip()
    # Split into the KEYPOINTS section and the TOPICS line.
    topics: list[str] = []
    tm = re.search(r"(?im)^\s*TOPICS?\s*:\s*(.+)$", text)
    if tm:
        raw = tm.group(1)
        for tok in re.split(r"[,\n]", raw):
            slug = tok.strip().strip("[]").split("/")[-1].strip().lower()
            slug = re.sub(r"[^a-z0-9-]", "", slug)
            if slug and slug in valid_topics and slug not in topics:
                topics.append(slug)
        text = text[: tm.start()]  # bullets are everything before TOPICS

    # Bullets: keep lines that look like list items.
    bullets = []
    for line in text.splitlines():
        s = line.strip()
        if s.upper().startswith("KEYPOINT"):
            continue
        if s.startswith(("-", "*", "•")):
            bullets.append("- " + s.lstrip("-*• ").strip())
    return "\n".join(bullets), topics[:3]


def run_node(fulltext: str, valid_topics: set[str]) -> NodeResult:
    ncfg = CONFIG["node"]
    prompt = (
        f"/{ncfg['skill']}\n\n"
        "Extract from the following paper. Output ONLY the KEYPOINTS bullets and "
        "the TOPICS line, nothing else.\n\n"
        "<paper>\n" + fulltext[:_MAX_CHARS] + "\n</paper>\n"
    )
    cmd = [ncfg["claude_bin"], "-p", "--model", ncfg["model"]]
    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=int(ncfg.get("timeout_seconds", 180)),
        )
    except FileNotFoundError:
        return NodeResult(error=f"claude not found at {ncfg['claude_bin']}")
    except subprocess.TimeoutExpired:
        return NodeResult(error="claude timed out")

    if proc.returncode != 0:
        return NodeResult(error=(proc.stderr or "claude returned non-zero").strip()[:300])

    bullets, topics = _parse(proc.stdout, valid_topics)
    if not bullets:
        return NodeResult(topics=topics, error="no bullets parsed from claude output")
    return NodeResult(bullets=bullets, topics=topics, ok=True)
