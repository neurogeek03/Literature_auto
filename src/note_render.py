"""Assemble the literature note from templates/note_layout.md and write it into
the vault. Deterministic given its inputs."""
from __future__ import annotations

import re
from pathlib import Path

from .config import ROOT, notes_dir
from .metadata import PaperMeta
from .node import NodeResult

_TEMPLATE = ROOT / "templates" / "note_layout.md"
_POSTER_TEMPLATE = ROOT / "templates" / "poster_layout.md"
_FM_DOI_RE = re.compile(r"(?m)^doi:\s*(.*)$")


def _one_line(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def render(
    meta: PaperMeta,
    fulltext_md: str,
    node: NodeResult,
    related_keys: list[str],
    code_links: list[str],
    conference: str = "",
) -> str:
    doi_url = f"https://doi.org/{meta.doi}" if meta.doi else (meta.url or "")
    preprint_link = ""
    if meta.url and meta.url != doi_url:
        # Preprint: use the biorxiv/medrxiv page as the primary (working) link;
        # keep doi.org as a secondary reference below.
        doi_url = meta.url
        preprint_link = f"> [DOI](https://doi.org/{meta.doi})" if meta.doi else ""
    topics = " ".join(f"[[topics/{t}]]" for t in node.topics)
    if conference:
        topics = (topics + " " if topics else "") + f"[[topics/conferences/{conference}]]"
    related = ", ".join(f"[[@{ck}]]" for ck in related_keys)
    code_section = (
        "### Code\n" + "\n".join(f"- {url}" for url in code_links)
        if code_links else ""
    )
    node_md = node.bullets if node.bullets else "- (no key points extracted)"

    pub_type = "preprint" if meta.is_preprint else "peer-reviewed"
    tags = pub_type + (f", conferences/{conference}" if conference else "")
    values = {
        "CITEKEY": meta.citekey,
        "YEAR": meta.year,
        "DOI": meta.doi,
        "DOI_URL": doi_url,
        "PREPRINT_LINK": preprint_link,
        "TITLE": _one_line(meta.title),
        "TOPICS": topics,
        "RELATED": related,
        "CODE_SECTION": code_section,
        "NODE": node_md,
        "ABSTRACT": _one_line(meta.abstract) or "(no abstract available)",
        "AUTHORS": "; ".join(meta.authors),
        "VENUE": _one_line(meta.venue),
        "FULLTEXT": fulltext_md.strip(),
        "PUB_TYPE": pub_type,
        "TAGS": tags,
    }
    md = _TEMPLATE.read_text()
    for key, val in values.items():
        md = md.replace("{{" + key + "}}", val)
    return md


def _existing_doi(path: Path) -> str:
    m = _FM_DOI_RE.search(path.read_text(errors="ignore"))
    return (m.group(1).strip() if m else "")


_FM_PUB_TYPE_RE = re.compile(r"(?m)^pub_type:\s*(\S+)")


def _is_poster_note(path: Path) -> bool:
    m = _FM_PUB_TYPE_RE.search(path.read_text(errors="ignore"))
    return bool(m) and m.group(1) in {"poster", "slide"}


def target_path(citekey: str, doi: str) -> Path:
    """Resolve the note path, disambiguating Author_Year collisions across
    different papers while allowing same-DOI updates to overwrite."""
    base = notes_dir() / f"@{citekey}.md"
    if not base.exists():
        return base
    if doi and _existing_doi(base) == doi:
        return base  # same paper -> update in place
    # Different paper, same Author_Year -> suffix a, b, c ...
    for suffix in "abcdefghijklmnopqrstuvwxyz":
        cand = notes_dir() / f"@{citekey}{suffix}.md"
        if not cand.exists():
            return cand
        if doi and _existing_doi(cand) == doi:
            return cand
    return base


def poster_target_path(citekey: str) -> Path:
    """Posters/slides have no DOI to dedupe on, so dedupe on the citekey itself
    (first author + year) instead: re-dropping the same person's poster updates
    the existing note in place. Never overwrites an existing *paper* note with
    the same citekey though (e.g. a real preprint by the same author/year) —
    that gets suffixed like a normal collision, since it isn't a re-drop."""
    base = notes_dir() / f"@{citekey}.md"
    if not base.exists():
        return base
    if _is_poster_note(base):
        return base  # same author's poster/slide -> update in place
    for suffix in "abcdefghijklmnopqrstuvwxyz":
        cand = notes_dir() / f"@{citekey}{suffix}.md"
        if not cand.exists():
            return cand
        if _is_poster_note(cand):
            return cand
    return base


def write_note(
    meta: PaperMeta,
    fulltext_md: str,
    node: NodeResult,
    related_keys: list[str],
    code_links: list[str],
    conference: str = "",
) -> Path:
    path = target_path(meta.citekey, meta.doi)
    # Keep the citekey in the note consistent with the (possibly suffixed) filename.
    meta.citekey = path.stem.lstrip("@")
    md = render(meta, fulltext_md, node, related_keys, code_links, conference=conference)
    path.write_text(md)
    return path


def render_poster(
    citekey: str,
    title: str,
    authors: list[str],
    year: str,
    venue: str,
    source_type: str,
    image_filename: str,
    topics: list[str],
    related_keys: list[str],
    node_bullets: str,
    conference: str = "",
) -> str:
    topics_line = " ".join(f"[[topics/{t}]]" for t in topics)
    if conference:
        topics_line = (topics_line + " " if topics_line else "") + f"[[topics/conferences/{conference}]]"
    related = ", ".join(f"[[@{ck}]]" for ck in related_keys)
    node_md = node_bullets if node_bullets else "- (no key points extracted)"
    tags = source_type + (f", conferences/{conference}" if conference else "")
    values = {
        "CITEKEY": citekey,
        "YEAR": year or "",
        "TITLE": _one_line(title) or "(untitled)",
        "TOPICS": topics_line,
        "RELATED": related,
        "NODE": node_md,
        "AUTHORS": "; ".join(authors) if authors else "Unknown",
        "VENUE": _one_line(venue) or "(venue not identified)",
        "SOURCE_TYPE": source_type,
        "SOURCE_LABEL": "Poster" if source_type == "poster" else "Slide",
        "IMAGE_FILENAME": image_filename,
        "TAGS": tags,
    }
    md = _POSTER_TEMPLATE.read_text()
    for key, val in values.items():
        md = md.replace("{{" + key + "}}", val)
    return md


def write_poster_note(
    citekey: str,
    title: str,
    authors: list[str],
    year: str,
    venue: str,
    source_type: str,
    image_filename: str,
    topics: list[str],
    related_keys: list[str],
    node_bullets: str,
    conference: str = "",
) -> Path:
    """Unlike write_note(), the caller resolves the citekey (via target_path)
    up front, since the image filename on disk is derived from it too."""
    path = notes_dir() / f"@{citekey}.md"
    md = render_poster(
        citekey, title, authors, year, venue, source_type,
        image_filename, topics, related_keys, node_bullets, conference=conference,
    )
    path.write_text(md)
    return path
