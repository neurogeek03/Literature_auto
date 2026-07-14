"""Assemble the literature note from templates/note_layout.md and write it into
the vault. Deterministic given its inputs."""
from __future__ import annotations

import re
from pathlib import Path

from .config import ROOT, notes_dir
from .metadata import PaperMeta
from .node import NodeResult

_TEMPLATE = ROOT / "templates" / "note_layout.md"
_FM_DOI_RE = re.compile(r"(?m)^doi:\s*(.*)$")


def _one_line(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def render(
    meta: PaperMeta,
    fulltext_md: str,
    node: NodeResult,
    related_keys: list[str],
    code_links: list[str],
) -> str:
    doi_url = f"https://doi.org/{meta.doi}" if meta.doi else (meta.url or "")
    preprint_link = ""
    if meta.url and meta.url != doi_url:
        # Preprint: use the biorxiv/medrxiv page as the primary (working) link;
        # keep doi.org as a secondary reference below.
        doi_url = meta.url
        preprint_link = f"> [DOI](https://doi.org/{meta.doi})" if meta.doi else ""
    topics = " ".join(f"[[topics/{t}]]" for t in node.topics)
    related = ", ".join(f"[[@{ck}]]" for ck in related_keys)
    code = ", ".join(code_links)
    node_md = node.bullets if node.bullets else "- (no key points extracted)"

    pub_type = "preprint" if meta.is_preprint else "peer-reviewed"
    values = {
        "CITEKEY": meta.citekey,
        "YEAR": meta.year,
        "DOI": meta.doi,
        "DOI_URL": doi_url,
        "PREPRINT_LINK": preprint_link,
        "TITLE": _one_line(meta.title),
        "TOPICS": topics,
        "RELATED": related,
        "CODE": code,
        "NODE": node_md,
        "ABSTRACT": _one_line(meta.abstract) or "(no abstract available)",
        "AUTHORS": "; ".join(meta.authors),
        "VENUE": _one_line(meta.venue),
        "FULLTEXT": fulltext_md.strip(),
        "PUB_TYPE": pub_type,
    }
    md = _TEMPLATE.read_text()
    for key, val in values.items():
        md = md.replace("{{" + key + "}}", val)
    return md


def _existing_doi(path: Path) -> str:
    m = _FM_DOI_RE.search(path.read_text(errors="ignore"))
    return (m.group(1).strip() if m else "")


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


def write_note(
    meta: PaperMeta,
    fulltext_md: str,
    node: NodeResult,
    related_keys: list[str],
    code_links: list[str],
) -> Path:
    path = target_path(meta.citekey, meta.doi)
    # Keep the citekey in the note consistent with the (possibly suffixed) filename.
    meta.citekey = path.stem.lstrip("@")
    md = render(meta, fulltext_md, node, related_keys, code_links)
    path.write_text(md)
    return path
