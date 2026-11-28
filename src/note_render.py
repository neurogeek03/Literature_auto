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


def _authors_line(authors: list[str], cap: int = 12) -> str:
    """Join authors, but collapse big consortium bylines (Science papers often
    carry 100+ Crossref author records) so the Metadata line stays readable."""
    if not authors:
        return ""
    if len(authors) <= cap:
        return "; ".join(authors)
    return "; ".join(authors[:cap]) + f"; et al. ({len(authors)} authors)"


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
        "AUTHORS": _authors_line(meta.authors),
        "VENUE": _one_line(meta.venue),
        "FULLTEXT": fulltext_md.strip(),
        "PUB_TYPE": pub_type,
        "TAGS": tags,
    }
    md = _TEMPLATE.read_text(encoding="utf-8")
    for key, val in values.items():
        md = md.replace("{{" + key + "}}", val)
    return md


def _existing_doi(path: Path) -> str:
    m = _FM_DOI_RE.search(path.read_text(errors="ignore", encoding="utf-8"))
    return (m.group(1).strip() if m else "")


_FM_PUB_TYPE_RE = re.compile(r"(?m)^pub_type:\s*(\S+)")


def _is_poster_note(path: Path) -> bool:
    m = _FM_PUB_TYPE_RE.search(path.read_text(errors="ignore", encoding="utf-8"))
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
    path.write_text(md, encoding="utf-8")
    return path


# --- preprint -> published in-place update -------------------------------------

# NB: match intra-line whitespace with [ \t], never \s — a blank field value
# (e.g. "dateread:") followed by \s* would swallow the newline and merge the
# next frontmatter line into the capture.
_FM_FIELD_RE = {
    "pub_type": re.compile(r"(?m)^pub_type:[ \t]*(.*)$"),
    "citekey": re.compile(r"(?m)^citekey:[ \t]*(.*)$"),
    "status": re.compile(r"(?m)^status:[ \t]*(.*)$"),
    "dateread": re.compile(r"(?m)^dateread:[ \t]*(.*)$"),
    "year": re.compile(r"(?m)^year:[ \t]*(.*)$"),
    "doi": re.compile(r"(?m)^doi:[ \t]*(.*)$"),
    "tags": re.compile(r"(?m)^tags:[ \t]*\[(.*)\][ \t]*$"),
}
_CONTRIBUTION_RE = re.compile(r"(?m)^(>\s*\*\*Contribution\*\*::)(.*)$")
_CALLOUT_FIELD_RE = {
    "Year": re.compile(r"(?m)^(>\s*\*\*Year\*\*::).*$"),
    "Journal": re.compile(r"(?m)^(>\s*\*\*Journal\*\*::).*$"),
    "Citekey": re.compile(r"(?m)^(>\s*\*\*Citekey\*\*::).*$"),
    "DOI": re.compile(r"(?m)^(>\s*\*\*DOI\*\*::).*$"),
}
_LINK_LINE_RE = re.compile(r"(?m)^(> \[[^\]]*\]\()[^)]*(\).*)$")
_PREPRINT_LINK_LINE_RE = re.compile(
    r"(?m)^> \[DOI\]\(https?://(?:dx\.)?doi\.org/[^)]*\)\n?"
)
_ABSTRACT_LINE_RE = re.compile(r"(?m)^(> \[!Abstract\]\n)(>.*)$")


def _fm_pub_type(path: Path) -> str:
    m = _FM_PUB_TYPE_RE.search(path.read_text(errors="ignore", encoding="utf-8"))
    return m.group(1) if m else ""


def find_note_by_doi(doi: str) -> Path | None:
    """The existing note whose frontmatter ``doi:`` equals ``doi`` (e.g. a
    preprint note we can update when its published version is dropped)."""
    if not doi:
        return None
    for f in sorted(notes_dir().glob("@*.md")):
        if _existing_doi(f) == doi:
            return f
    return None


def _norm_title(s: str) -> str:
    s = re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())
    return re.sub(r"\s+", " ", s).strip()


def _last_name(author: str) -> str:
    if not author:
        return ""
    last = author.split(",")[0] if "," in author else author.split()[-1]
    return re.sub(r"[^a-z]", "", last.lower())


_NOTE_TITLE_RE = re.compile(r"(?m)^>\s*\*\*Title\*\*::\s*(.+)$")
_NOTE_AUTHORS_RE = re.compile(r"(?m)^>\s*\*\*Authors\*\*::\s*(.+)$")


def find_preprint_by_title(meta: PaperMeta) -> Path | None:
    """Fallback match when no DOI relation is available: an existing *preprint*
    note with the exact same (normalized) title AND the same first-author last
    name. Tightly gated so a published drop never clobbers an unrelated note."""
    want_title = _norm_title(meta.title)
    if not want_title or not meta.authors:
        return None
    want_last = _last_name(meta.authors[0])
    for f in sorted(notes_dir().glob("@*.md")):
        if _fm_pub_type(f) != "preprint":
            continue
        txt = f.read_text(errors="ignore", encoding="utf-8")
        mt = _NOTE_TITLE_RE.search(txt)
        if not mt or _norm_title(mt.group(1)) != want_title:
            continue
        ma = _NOTE_AUTHORS_RE.search(txt)
        note_last = _last_name(ma.group(1).split(";")[0]) if ma else ""
        if want_last and note_last and want_last == note_last:
            return f
    return None


def _preserve_user_fields(old_text: str) -> dict:
    """The user's reading state, which a re-render would otherwise reset."""
    out = {}
    for key in ("status", "dateread"):
        m = _FM_FIELD_RE[key].search(old_text)
        out[key] = m.group(1).strip() if m else ""
    mc = _CONTRIBUTION_RE.search(old_text)
    out["contribution"] = mc.group(2).strip() if mc else ""
    return out


def _apply_preserved(md: str, fields: dict) -> str:
    md = _FM_FIELD_RE["status"].sub(f"status: {fields.get('status', '')}".rstrip(), md, count=1)
    md = _FM_FIELD_RE["dateread"].sub(f"dateread: {fields.get('dateread', '')}".rstrip(), md, count=1)
    contrib = fields.get("contribution", "")
    if contrib:
        md = _CONTRIBUTION_RE.sub(lambda m: f"{m.group(1)} {contrib}", md, count=1)
    return md


def _apply_published_metadata(old_text: str, meta: PaperMeta) -> str:
    """Surgically flip only the metadata on an existing preprint note so its
    node / full text / related / user edits survive untouched (the fail-safe
    path when the published full text isn't accessible)."""
    md = old_text
    md = _FM_FIELD_RE["pub_type"].sub("pub_type: peer-reviewed", md, count=1)
    md = _FM_FIELD_RE["tags"].sub(
        lambda m: "tags: [" + re.sub(r"\bpreprint\b", "peer-reviewed", m.group(1)) + "]",
        md, count=1,
    )
    md = _FM_FIELD_RE["citekey"].sub(f"citekey: {meta.citekey}", md, count=1)
    if meta.year:
        md = _FM_FIELD_RE["year"].sub(f"year: {meta.year}", md, count=1)
    md = _FM_FIELD_RE["doi"].sub(f"doi: {meta.doi}", md, count=1)

    doi_url = f"https://doi.org/{meta.doi}" if meta.doi else (meta.url or "")
    md = _LINK_LINE_RE.sub(lambda m: f"{m.group(1)}{doi_url}{m.group(2)}", md, count=1)
    # No longer a preprint: drop the secondary preprint DOI line.
    md = _PREPRINT_LINK_LINE_RE.sub("", md, count=1)

    md = _CALLOUT_FIELD_RE["Citekey"].sub(lambda m: f"{m.group(1)} {meta.citekey}", md, count=1)
    md = _CALLOUT_FIELD_RE["DOI"].sub(lambda m: f"{m.group(1)} {meta.doi}", md, count=1)
    if meta.year:
        md = _CALLOUT_FIELD_RE["Year"].sub(lambda m: f"{m.group(1)} {meta.year}", md, count=1)
    if meta.venue:
        venue = _one_line(meta.venue)
        md = _CALLOUT_FIELD_RE["Journal"].sub(lambda m: f"{m.group(1)} {venue}", md, count=1)
    if meta.abstract:
        abs = _one_line(meta.abstract)
        md = _ABSTRACT_LINE_RE.sub(lambda m: f"{m.group(1)}> {abs}", md, count=1)
    return md


def rewrite_backlinks(old_ck: str, new_ck: str, skip: Path | None = None) -> int:
    """Rewrite every [[@old_ck]] / [[@old_ck|alias]] / [[@old_ck#heading]] link
    across the vault to the new citekey. Token-safe so @Smith_2024 never matches
    inside @Smith_20240. Returns the number of files changed."""
    if old_ck == new_ck:
        return 0
    pat = re.compile(r"\[\[@" + re.escape(old_ck) + r"(?=[\]|#])")
    repl = f"[[@{new_ck}"
    changed = 0
    for f in sorted(notes_dir().glob("@*.md")):
        if skip and f == skip:
            continue
        txt = f.read_text(errors="ignore", encoding="utf-8")
        new = pat.sub(repl, txt)
        if new != txt:
            f.write_text(new, encoding="utf-8")
            changed += 1
    return changed


def update_published(
    old_path: Path,
    meta: PaperMeta,
    fulltext_md: str,
    node: NodeResult,
    related_keys: list[str],
    code_links: list[str],
    conference: str = "",
    metadata_only: bool = False,
) -> Path:
    """Update an existing preprint note in place for its published version:
    rename @Author_PreprintYear.md -> @Author_PubYear.md, refresh content (or
    just flip metadata when metadata_only), rewrite backlinks, and always
    preserve the user's status / dateread / Contribution edits."""
    old_text = old_path.read_text(encoding="utf-8")
    old_ck = old_path.stem.lstrip("@")
    preserved = _preserve_user_fields(old_text)

    # Resolve the destination. If the published citekey is unchanged (preprint
    # and journal share a year), update the same file — don't let target_path
    # suffix it just because the DOI changed. Otherwise target_path picks the new
    # @Author_PubYear.md, suffixing only against a *different* existing paper.
    base = notes_dir() / f"@{meta.citekey}.md"
    new_path = old_path if base == old_path else target_path(meta.citekey, meta.doi)
    meta.citekey = new_path.stem.lstrip("@")

    if metadata_only:
        # Keeps the old node / full text / related; an absent publisher abstract
        # leaves the preprint abstract untouched (handled in the applier).
        md = _apply_published_metadata(old_text, meta)
    else:
        md = render(meta, fulltext_md, node, related_keys, code_links, conference=conference)
        md = _apply_preserved(md, preserved)

    new_path.write_text(md, encoding="utf-8")
    if new_path != old_path and old_path.exists():
        old_path.unlink()
    rewrite_backlinks(old_ck, meta.citekey, skip=new_path)
    return new_path


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
    md = _POSTER_TEMPLATE.read_text(encoding="utf-8")
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
    path.write_text(md, encoding="utf-8")
    return path
