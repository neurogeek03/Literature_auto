"""Deterministic parsing of a caption typed alongside a PDF/DOI drop into a
conference tag (from a whitelist in config.yaml) + a free-text focus hint for
the node. See IMPROVEMENTS.md #4 for the full spec.
"""
from __future__ import annotations

import re

from .config import CONFIG, topics_dir

_SEPARATORS = ("—", ",", ";")


def _conferences() -> list[dict]:
    return CONFIG.get("conferences") or []


def _normalize(token: str) -> str:
    """spaces -> underscores; glued or separated 2-digit year -> 4-digit; upper."""
    s = token.strip()
    s = re.sub(r"\s+", "_", s)
    m = re.match(r"^([A-Za-z]+)[_-]?(\d{2})$", s)
    if m:
        s = f"{m.group(1)}_20{m.group(2)}"
    else:
        m = re.match(r"^([A-Za-z]+)[_-]?(\d{4})$", s)
        if m:
            s = f"{m.group(1)}_{m.group(2)}"
    return s.upper()


def _match_exact(candidate: str) -> str:
    """Steps 1-2 only: exact or normalized slug/alias match. No fuzzy
    substring step -- safe to run against every token window in a free-form
    sentence without false-positiving on short incidental words."""
    candidate = candidate.strip()
    if not candidate:
        return ""
    cand_lower = candidate.lower()

    # 1. Exact match against slugs and aliases (case-insensitive).
    for c in _conferences():
        if cand_lower == c["slug"].lower():
            return c["slug"]
        for alias in c.get("aliases", []):
            if cand_lower == alias.lower():
                return c["slug"]

    # 2. Normalize (spaces->underscore, 2-digit->4-digit year, uppercase) and re-check.
    norm = _normalize(candidate)
    for c in _conferences():
        if norm == c["slug"].upper():
            return c["slug"]
        for alias in c.get("aliases", []):
            if norm == _normalize(alias):
                return c["slug"]
    return ""


def _match(candidate: str) -> str:
    """Return the matched slug, or '' if nothing matches uniquely."""
    slug = _match_exact(candidate)
    if slug:
        return slug

    # 3. Unique prefix/substring match against slugs. Only safe when the
    # candidate is a deliberately-typed leading token (the terse "CONF —
    # hint" convention or a bare caption), never against arbitrary words
    # pulled out of a natural-language sentence -- see _find_anywhere, which
    # deliberately does NOT use this step.
    norm = _normalize(candidate.strip())
    # Guard: a 1-2 char candidate ("a", "in", "no", ...) can trivially
    # substring-match inside a slug (e.g. "a" is inside "C-A-N_2026") by pure
    # coincidence. No real conference alias is that short (shortest is "CAN",
    # 3 chars), so anything shorter can't be a deliberate attempt.
    if not norm or len(norm) < 3:
        return ""
    matches = [
        c["slug"] for c in _conferences()
        if c["slug"].upper().startswith(norm) or norm in c["slug"].upper()
    ]
    if len(matches) == 1:
        return matches[0]
    return ""


def _find_anywhere(text: str) -> str:
    """Scan every token window in text (not just a leading prefix) for a
    unique whitelist hit. Used when the terse 'CONF — hint' convention wasn't
    used, e.g. a natural-language photo caption like 'This was from CAN-2026'
    where the conference token isn't at the start of the sentence. Uses
    _match_exact (not _match): the fuzzy substring step is only safe against
    a deliberately-typed leading token, not arbitrary words in a sentence."""
    tokens = text.split()
    candidates = set()
    for start in range(len(tokens)):
        for n in range(1, min(4, len(tokens) - start) + 1):
            slug = _match_exact(" ".join(tokens[start : start + n]))
            if slug:
                candidates.add(slug)
    return candidates.pop() if len(candidates) == 1 else ""


def parse_prompt(text: str) -> tuple[str, str]:
    """(conference_slug_or_empty, focus_hint). See IMPROVEMENTS.md #4 for spec."""
    text = (text or "").strip()
    if not text:
        return "", ""

    split = None
    for sep in _SEPARATORS:
        idx = text.find(sep)
        if idx != -1 and (split is None or idx < split[0]):
            split = (idx, sep)

    if split is not None:
        idx, sep = split
        head, tail = text[:idx].strip(), text[idx + len(sep):].strip()
        slug = _match(head)
        return (slug, tail) if slug else ("", text)

    # No separator: try progressively longer whitespace-delimited prefixes
    # (conference names/aliases can be multiple tokens, e.g. "ISMB 26").
    tokens = text.split()
    best_slug, best_len = "", 0
    for n in range(1, min(4, len(tokens) + 1)):
        slug = _match(" ".join(tokens[:n]))
        if slug:
            best_slug, best_len = slug, n
    if best_slug:
        return best_slug, " ".join(tokens[best_len:]).strip()

    # Prefix didn't match -- natural-language caption (e.g. "This was from
    # CAN-2026"). Scan anywhere in the text for an unambiguous whitelist hit;
    # keep the whole caption as the focus hint rather than trying to carve
    # the matched words back out of a sentence.
    anywhere = _find_anywhere(text)
    if anywhere:
        return anywhere, text
    return "", text


def ensure_stub(slug: str) -> None:
    """Create topics/conferences/<slug>.md (and the conferences/ subdir) if missing."""
    sub_dir = topics_dir() / "conferences"
    sub_dir.mkdir(parents=True, exist_ok=True)
    stub = sub_dir / f"{slug}.md"
    if stub.exists():
        return
    stub.write_text(
        f"> [!topic] {slug}\n"
        f"> Part of [[topics/conferences]]\n"
    )
