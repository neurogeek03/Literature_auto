"""Deterministic code-availability extraction (regex, no LLM)."""
from __future__ import annotations

import re

# Code-hosting URLs worth surfacing.
_HOST_RE = re.compile(
    r"https?://(?:www\.)?(?:github\.com|gitlab\.com|bitbucket\.org|"
    r"zenodo\.org|huggingface\.co|codeocean\.com|figshare\.com)/[^\s)\]}>\"']+",
    re.IGNORECASE,
)
# Trailing punctuation that commonly glues onto URLs in PDF text.
_TRAIL = ".,);]}>\"'"


def find_code_links(text: str, limit: int = 5) -> list[str]:
    if not text:
        return []
    seen: list[str] = []
    for m in _HOST_RE.finditer(text):
        url = m.group(0).rstrip(_TRAIL)
        # Drop obvious asset/badge noise.
        if url.lower().endswith((".png", ".svg", ".jpg", ".gif")):
            continue
        if url not in seen:
            seen.append(url)
        if len(seen) >= limit:
            break
    return seen
