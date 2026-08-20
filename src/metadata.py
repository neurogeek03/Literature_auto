"""Deterministic metadata: DOI -> Crossref/OpenAlex -> clean fields + citekey.

No LLM. Same input -> same output.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

import requests

from .config import CONFIG

# DOIs: 10.<registrant>/<suffix>. Trailing punctuation trimmed after match.
DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")


PREPRINT_VENUES = {"biorxiv", "medrxiv", "arxiv", "chemrxiv", "ssrn", "researchsquare"}


@dataclass
class PaperMeta:
    title: str = ""
    authors: list[str] = field(default_factory=list)  # "Last, First"
    year: str = ""
    venue: str = ""
    abstract: str = ""
    doi: str = ""
    url: str = ""
    oa_pdf_url: str = ""
    citekey: str = ""
    is_preprint: bool = False


# Explicit "DOI: 10.xxx" label — marks the article's *own* DOI in end-matter,
# as opposed to the bare/"doi.org/" DOIs of cited references.
DOI_LABEL_RE = re.compile(r"(?i)\bdoi:\s*(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)")


def _clean_doi(doi: str) -> str:
    # Strip common trailing artifacts from PDF text extraction.
    doi = doi.rstrip(").,;")
    # Drop an accidental trailing 'pdf' glued on by some extractors.
    doi = re.sub(r"(?i)pdf$", "", doi).rstrip(").,;")
    return doi


def find_doi(text: str) -> str:
    """The article's *own* DOI, not the first DOI-looking token.

    Journals like Science/Sci. Transl. Med./Sci. Adv. print the article DOI only
    in the end-matter, *after* the reference list — so the first DOI in the text
    is usually a cited reference's DOI. We instead score every DOI by how often
    it occurs (the article's own repeats in page footers / citation block) with a
    strong bonus for any appearance next to an explicit "DOI:" label, and break
    ties by earliest position (page-1 DOI wins for normal journals).
    """
    if not text:
        return ""
    score: dict[str, int] = {}
    first_pos: dict[str, int] = {}
    for m in DOI_RE.finditer(text):
        doi = _clean_doi(m.group(0))
        if not doi:
            continue
        score[doi] = score.get(doi, 0) + 1
        first_pos.setdefault(doi, m.start())
    if not score:
        return ""
    for m in DOI_LABEL_RE.finditer(text):
        doi = _clean_doi(m.group(1))
        if doi in score:
            score[doi] += 5
    return max(score, key=lambda d: (score[d], -first_pos[d]))


def _strip_tags(s: str) -> str:
    return re.sub(r"\s+", " ", TAG_RE.sub(" ", s or "")).strip()


def _mailto() -> str:
    return (CONFIG.get("metadata") or {}).get("crossref_mailto", "")


def fetch_crossref(doi: str) -> PaperMeta | None:
    url = f"https://api.crossref.org/works/{requests.utils.quote(doi)}"
    params = {"mailto": _mailto()} if _mailto() else {}
    try:
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        m = r.json()["message"]
    except Exception:
        return None

    authors = []
    for a in m.get("author", []) or []:
        last = a.get("family", "").strip()
        first = a.get("given", "").strip()
        if last and first:
            authors.append(f"{last}, {first}")
        elif last:
            authors.append(last)
        elif a.get("name"):
            authors.append(a["name"].strip())

    year = ""
    for key in ("published-print", "published-online", "issued", "created"):
        parts = (m.get(key) or {}).get("date-parts") or []
        if parts and parts[0] and parts[0][0]:
            year = str(parts[0][0])
            break

    title = " ".join(m.get("title") or []).strip()
    venue = " ".join(m.get("container-title") or []).strip()
    abstract = _strip_tags(m.get("abstract", ""))

    is_preprint = m.get("type") == "posted-content"
    return PaperMeta(
        title=title,
        authors=authors,
        year=year,
        venue=venue,
        abstract=abstract,
        doi=doi,
        url=m.get("URL", f"https://doi.org/{doi}"),
        is_preprint=is_preprint,
    )


def _openalex_abstract(inv_index: dict) -> str:
    if not inv_index:
        return ""
    positions: list[tuple[int, str]] = []
    for word, idxs in inv_index.items():
        for i in idxs:
            positions.append((i, word))
    positions.sort()
    return " ".join(w for _, w in positions)


def fetch_openalex(doi: str) -> PaperMeta | None:
    url = f"https://api.openalex.org/works/https://doi.org/{doi}"
    params = {"mailto": _mailto()} if _mailto() else {}
    try:
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        w = r.json()
    except Exception:
        return None

    authors = []
    for a in w.get("authorships", []) or []:
        name = (a.get("author") or {}).get("display_name", "").strip()
        if name:
            authors.append(name)

    oa = w.get("best_oa_location") or w.get("primary_location") or {}
    return PaperMeta(
        title=(w.get("title") or "").strip(),
        authors=authors,
        year=str(w.get("publication_year") or ""),
        venue=((w.get("primary_location") or {}).get("source") or {}).get("display_name", "") or "",
        abstract=_openalex_abstract(w.get("abstract_inverted_index")),
        doi=doi,
        url=w.get("id", f"https://doi.org/{doi}"),
        oa_pdf_url=(oa.get("pdf_url") or "") if isinstance(oa, dict) else "",
        is_preprint=w.get("type") == "preprint",
    )


def fetch_biorxiv(doi: str) -> PaperMeta | None:
    """Fallback for preprints on biorxiv/medrxiv not yet indexed in Crossref/OpenAlex."""
    for server in ("biorxiv", "medrxiv"):
        try:
            r = requests.get(
                f"https://api.biorxiv.org/details/{server}/{doi}/na/1", timeout=20
            )
            r.raise_for_status()
            items = r.json().get("collection") or []
            if not items:
                continue
            d = items[0]
            raw_authors = d.get("authors", "")
            authors = [a.strip() for a in raw_authors.split(";") if a.strip()]
            year = (d.get("date") or "")[:4]
            base_url = f"https://www.{server}.org/content/{doi}"
            return PaperMeta(
                title=d.get("title", "").strip(),
                authors=authors,
                year=year,
                venue=d.get("server", "bioRxiv"),
                abstract=d.get("abstract", "").strip(),
                doi=doi,
                url=base_url,
                is_preprint=True,
            )
        except Exception:
            continue
    return None


def fetch_unpaywall_pdf(doi: str) -> str:
    email = (CONFIG.get("metadata") or {}).get("unpaywall_email", "")
    if not email:
        return ""
    try:
        r = requests.get(
            f"https://api.unpaywall.org/v2/{doi}", params={"email": email}, timeout=20
        )
        r.raise_for_status()
        loc = r.json().get("best_oa_location") or {}
        return loc.get("url_for_pdf") or ""
    except Exception:
        return ""


def _ascii_last_name(author: str) -> str:
    """'Last, First' or 'First Last' -> ascii last-name token."""
    if not author:
        return "Unknown"
    last = author.split(",")[0].strip() if "," in author else author.split()[-1]
    norm = unicodedata.normalize("NFKD", last).encode("ascii", "ignore").decode()
    norm = re.sub(r"[^A-Za-z]", "", norm)
    return norm.capitalize() or "Unknown"


def make_citekey(meta: PaperMeta) -> str:
    last = _ascii_last_name(meta.authors[0]) if meta.authors else "Unknown"
    year = meta.year or "ND"
    return f"{last}_{year}"


def get_metadata(doi: str = "", pdf_text: str = "", doi_text: str | None = None) -> PaperMeta:
    """Resolve metadata. Prefers Crossref, backfills from OpenAlex, falls back
    to a title guessed from the PDF text when no DOI is available.

    ``pdf_text`` (the first pages) drives the title fallback; ``doi_text`` is the
    text searched for a DOI — pass the *full* document so a DOI that only appears
    in the end-matter (Science journals) is found. Defaults to ``pdf_text``.
    """
    doi = doi or find_doi(doi_text if doi_text is not None else pdf_text)

    meta: PaperMeta | None = None
    if doi:
        meta = fetch_crossref(doi)
        oa = fetch_openalex(doi)
        if meta and oa:
            # Backfill anything Crossref lacks (esp. abstract, OA pdf).
            meta.abstract = meta.abstract or oa.abstract
            meta.oa_pdf_url = oa.oa_pdf_url
            meta.venue = meta.venue or oa.venue
            if not meta.authors:
                meta.authors = oa.authors
            meta.is_preprint = meta.is_preprint or oa.is_preprint
        elif oa and not meta:
            meta = oa
        # Preprints (biorxiv/medrxiv) are often absent from Crossref/OpenAlex.
        if not meta:
            meta = fetch_biorxiv(doi)
        if meta and not meta.oa_pdf_url:
            meta.oa_pdf_url = fetch_unpaywall_pdf(doi)

    if meta is None:
        meta = PaperMeta(doi=doi, url=f"https://doi.org/{doi}" if doi else "")
        # Best-effort title from the first non-empty line of the PDF text.
        for line in (pdf_text or "").splitlines():
            line = line.strip()
            if len(line) > 15:
                meta.title = line
                break

    # Venue-name fallback: catch preprints that lack a typed field.
    if not meta.is_preprint and meta.venue:
        meta.is_preprint = any(v in meta.venue.lower() for v in PREPRINT_VENUES)

    meta.citekey = make_citekey(meta)
    return meta
