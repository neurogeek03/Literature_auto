"""Deterministic metadata: DOI -> Crossref/OpenAlex -> clean fields + citekey.

No LLM. Same input -> same output.
"""
from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import requests

from .config import CONFIG

# DOIs: 10.<registrant>/<suffix>. Trailing punctuation trimmed after match.
DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")

# arXiv IDs. Modern scheme is YYMM.NNNNN (4-5 digit sequence), optional version
# (v1, v2, ...). Old scheme is <archive>[.<subclass>]/YYMMNNN. arXiv preprints
# print NO DOI of their own in the body, so relying on find_doi() would grab a
# *cited reference's* DOI — we detect the arXiv ID and resolve it directly.
ARXIV_MODERN = r"\d{4}\.\d{4,5}(?:v\d+)?"
ARXIV_OLD = r"(?:[a-z-]+(?:\.[A-Z]{2})?)/\d{7}(?:v\d+)?"
# In free text, require an explicit "arXiv:" context token so a random dotted
# number (or a cited-ref DOI) is never misread as an arXiv ID.
ARXIV_TEXT_RE = re.compile(
    rf"(?i)arxiv[:\s]*({ARXIV_MODERN}|{ARXIV_OLD})"
)
# arxiv.org/abs|pdf/<id> URLs are unambiguous on their own.
ARXIV_URL_RE = re.compile(
    rf"(?i)arxiv\.org/(?:abs|pdf)/({ARXIV_MODERN}|{ARXIV_OLD})"
)
# A bare id used only for source_hint (filename stem / caption), where the
# presence of an arXiv-shaped token is a strong enough signal on its own.
ARXIV_BARE_RE = re.compile(rf"(?<!\d)({ARXIV_MODERN})(?!\d)")

# A real arXiv paper stamps its own id in the page-1 left margin, extracted near
# the very start of the text; a *cited* arXiv id lives in the reference list at
# the end. So we only trust a free-text "arXiv:" token in this leading window,
# otherwise a non-arXiv paper that merely cites an arXiv preprint gets its
# identity hijacked by the citation (the AlphaGenome-medRxiv-cites-GELU bug).
ARXIV_HEAD_CHARS = 2500


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
    # Set by fetch_arxiv when the arXiv record links a published journal DOI, so
    # get_metadata can prefer the richer Crossref/OpenAlex record.
    journal_doi: str = ""


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


# medRxiv/bioRxiv stamp every page with "medRxiv preprint doi: https://doi.org/<DOI>"
# — an unambiguous *self* identity a cited reference can never spoof. Matched on the
# label, so it is prefix-agnostic (classic 10.1101 and newer prefixes like 10.64898).
PREPRINT_DOI_LABEL_RE = re.compile(
    r"(?i)\b(med|bio)rxiv\s+preprint\s+doi:\s*"
    r"(?:https?://(?:dx\.)?doi\.org/)?(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)"
)


def find_preprint_doi(text: str) -> tuple[str, str]:
    """A medRxiv/bioRxiv preprint's OWN DOI, read from the page running-header
    stamp printed on every page. Returns ``(doi, server)`` where server is
    "medrxiv"/"biorxiv", or ``("", "")`` if no such stamp is present.

    Unlike ``find_doi``, this cannot be fooled by a cited reference: only the
    server's own page banner carries this exact label. Returns the most frequent
    stamped DOI (robust against a stray one-off), tie-broken by the classic
    running-header form.
    """
    if not text:
        return "", ""
    counts: dict[str, int] = {}
    server_of: dict[str, str] = {}
    for m in PREPRINT_DOI_LABEL_RE.finditer(text):
        doi = _clean_doi(m.group(2))
        if not doi:
            continue
        counts[doi] = counts.get(doi, 0) + 1
        server_of.setdefault(doi, f"{m.group(1).lower()}rxiv")
    if not counts:
        return "", ""
    best = max(counts, key=lambda d: counts[d])
    return best, server_of[best]


def _norm_arxiv_id(raw: str) -> str:
    """Strip a trailing version suffix (v1, v2, ...) and lowercase old-scheme."""
    raw = raw.strip()
    raw = re.sub(r"(?i)v\d+$", "", raw)
    return raw


def find_arxiv_id(text: str = "", source_hint: str = "") -> str:
    """The paper's arXiv ID, or '' if this isn't an arXiv preprint.

    arXiv PDFs carry no DOI of their own in the body, so ``find_doi`` would pick
    a cited reference's DOI. We look, in order, for:
    1. an explicit ``arXiv:<id>`` token in the document text,
    2. an ``arxiv.org/abs|pdf/<id>`` URL in the text,
    3. an ``arxiv.org`` URL in ``source_hint`` (a shared link),
    4. a bare arXiv-shaped id in ``source_hint`` (the download filename stem,
       which browsers/Slack name after the id, e.g. ``2602.11632.pdf``).

    The free-text match (1) requires the ``arXiv:`` context token AND must fall
    within the leading ``ARXIV_HEAD_CHARS`` (the page-1 margin stamp region), so a
    cited arXiv reference in the bibliography of a non-arXiv paper is ignored. The
    source_hint bare match (4) only runs on the hint, so a stray dotted number or a
    cited-ref DOI in the body never registers as an arXiv ID.
    """
    if text:
        head = text[:ARXIV_HEAD_CHARS]
        m = ARXIV_TEXT_RE.search(head) or ARXIV_URL_RE.search(head)
        if m:
            return _norm_arxiv_id(m.group(1))
    if source_hint:
        m = ARXIV_URL_RE.search(source_hint) or ARXIV_TEXT_RE.search(source_hint)
        if m:
            return _norm_arxiv_id(m.group(1))
        m = ARXIV_BARE_RE.search(source_hint)
        if m:
            return _norm_arxiv_id(m.group(1))
    return ""


_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV_NS = "{http://arxiv.org/schemas/atom}"


def _arxiv_ua() -> str:
    return f"paper-pipeline/1.0 (mailto:{_mailto()})" if _mailto() else "paper-pipeline/1.0"


def fetch_arxiv(arxiv_id: str) -> PaperMeta | None:
    """Resolve arXiv metadata. Tries the export API (Atom XML) first, then falls
    back to the abs-page citation meta tags (the export API is occasionally slow
    or rate-limited). None only if both fail.
    """
    if not arxiv_id:
        return None
    return _fetch_arxiv_api(arxiv_id) or _fetch_arxiv_abs(arxiv_id)


def _fetch_arxiv_api(arxiv_id: str) -> PaperMeta | None:
    try:
        r = requests.get(
            "https://export.arxiv.org/api/query",
            params={"id_list": arxiv_id, "max_results": 1},
            headers={"User-Agent": _arxiv_ua()},
            timeout=20,
        )
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except Exception:
        return None

    entry = root.find(f"{_ATOM}entry")
    if entry is None:
        return None
    # A bad/unknown id returns a placeholder entry with no title/published.
    title = (entry.findtext(f"{_ATOM}title") or "").strip()
    published = (entry.findtext(f"{_ATOM}published") or "").strip()
    if not title or not published:
        return None
    title = re.sub(r"\s+", " ", title)

    authors: list[str] = []
    for a in entry.findall(f"{_ATOM}author"):
        name = (a.findtext(f"{_ATOM}name") or "").strip()
        if not name:
            continue
        # arXiv gives "First Last"; normalize to "Last, First".
        parts = name.split()
        if len(parts) >= 2:
            authors.append(f"{parts[-1]}, {' '.join(parts[:-1])}")
        else:
            authors.append(name)

    year = published[:4]
    abstract = re.sub(r"\s+", " ", (entry.findtext(f"{_ATOM}summary") or "").strip())
    journal_doi = _clean_doi((entry.findtext(f"{_ARXIV_NS}doi") or "").strip())

    return PaperMeta(
        title=title,
        authors=authors,
        year=year,
        venue="arXiv",
        abstract=abstract,
        doi=f"10.48550/arXiv.{arxiv_id}",
        url=f"https://arxiv.org/abs/{arxiv_id}",
        oa_pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
        is_preprint=True,
        journal_doi=journal_doi,
    )


def _meta_tag(html: str, name: str) -> list[str]:
    """All <meta name=... content=...> values for a citation tag (order-agnostic)."""
    pat = re.compile(
        rf'<meta[^>]+name=["\']{re.escape(name)}["\'][^>]*?content=["\']([^"\']*)["\']'
        rf'|<meta[^>]+content=["\']([^"\']*)["\'][^>]*?name=["\']{re.escape(name)}["\']',
        re.IGNORECASE,
    )
    out = []
    for m in pat.finditer(html):
        out.append(m.group(1) if m.group(1) is not None else m.group(2))
    return out


def _fetch_arxiv_abs(arxiv_id: str) -> PaperMeta | None:
    """Fallback: parse Highwire citation_* meta tags from the abs page HTML."""
    try:
        r = requests.get(
            f"https://arxiv.org/abs/{arxiv_id}",
            headers={"User-Agent": _arxiv_ua()},
            timeout=20,
        )
        r.raise_for_status()
        html = r.text
    except Exception:
        return None

    titles = _meta_tag(html, "citation_title")
    if not titles:
        return None
    authors = _meta_tag(html, "citation_author")  # already "Last, First"
    dates = _meta_tag(html, "citation_date") or _meta_tag(html, "citation_online_date")
    year = ""
    if dates:
        m = re.search(r"\d{4}", dates[0])
        year = m.group(0) if m else ""
    abstract = ""
    descs = _meta_tag(html, "citation_abstract") or _meta_tag(html, "og:description")
    if descs:
        abstract = re.sub(r"\s+", " ", descs[0]).strip()
    journal_doi = _clean_doi((_meta_tag(html, "citation_doi") or [""])[0].strip())

    return PaperMeta(
        title=re.sub(r"\s+", " ", titles[0]).strip(),
        authors=[a.strip() for a in authors if a.strip()],
        year=year,
        venue="arXiv",
        abstract=abstract,
        doi=f"10.48550/arXiv.{arxiv_id}",
        url=f"https://arxiv.org/abs/{arxiv_id}",
        oa_pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
        is_preprint=True,
        journal_doi=journal_doi,
    )


def _strip_tags(s: str) -> str:
    return re.sub(r"\s+", " ", TAG_RE.sub(" ", s or "")).strip()


# Some deposits (e.g. medRxiv) inject role labels as fake author entries
# ("Lead authors:", "Senior authors:", "Corresponding author:"). These corrupt
# the first-author citekey, so drop them before building metadata.
_AUTHOR_JUNK_RE = re.compile(
    r"(?i)^(lead|senior|corresponding|contributing|co[- ]?)?\s*authors?\s*:?\s*$"
)


def _clean_authors(authors: list[str]) -> list[str]:
    out: list[str] = []
    for a in authors:
        a = (a or "").strip().strip(",").strip()
        if not a or a.endswith(":") or _AUTHOR_JUNK_RE.match(a):
            continue
        if not re.search(r"[A-Za-z]", a):  # no actual name characters
            continue
        out.append(a)
    return out


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

    title = _strip_tags(" ".join(m.get("title") or []))
    venue = " ".join(m.get("container-title") or []).strip()
    abstract = _strip_tags(m.get("abstract", ""))

    is_preprint = m.get("type") == "posted-content"
    return PaperMeta(
        title=title,
        authors=_clean_authors(authors),
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
        title=_strip_tags(w.get("title") or ""),
        authors=_clean_authors(authors),
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
            authors = _clean_authors(raw_authors.split(";"))
            year = (d.get("date") or "")[:4]
            base_url = f"https://www.{server}.org/content/{doi}"
            return PaperMeta(
                title=_strip_tags(d.get("title", "")),
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


def _resolve_by_doi(doi: str) -> PaperMeta | None:
    """Crossref first, backfilled by OpenAlex, bioRxiv fallback, Unpaywall OA."""
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
    return meta


def get_metadata(
    doi: str = "",
    pdf_text: str = "",
    doi_text: str | None = None,
    arxiv_id: str = "",
    source_hint: str = "",
) -> PaperMeta:
    """Resolve metadata. Prefers Crossref, backfills from OpenAlex, falls back
    to a title guessed from the PDF text when no DOI is available.

    ``pdf_text`` (the first pages) drives the title fallback; ``doi_text`` is the
    text searched for a DOI — pass the *full* document so a DOI that only appears
    in the end-matter (Science journals) is found. Defaults to ``pdf_text``.

    Precedence: an explicit ``doi`` (user was deliberate) wins; else a
    medRxiv/bioRxiv self-DOI read from the page banner (authoritative — a cited
    reference can't spoof it, and it wins over any arXiv reference the preprint
    happens to cite); else an arXiv ID (``arxiv_id`` or one detected in the header
    region / ``source_hint``) resolves against the arXiv API — never via
    ``find_doi``, which would grab a cited reference's DOI on a preprint; else the
    normal ``find_doi``/Crossref flow.
    """
    search_text = doi_text if doi_text is not None else pdf_text

    meta: PaperMeta | None = None

    # A medRxiv/bioRxiv preprint's own banner DOI is authoritative and must be
    # checked before arXiv detection: a preprint that cites an arXiv paper (e.g.
    # AlphaGenome medRxiv citing GELU arXiv) would otherwise be hijacked by the
    # cited arXiv id.
    preprint_doi = ""
    preprint_server = ""
    if not doi:
        preprint_doi, preprint_server = find_preprint_doi(search_text)
        if preprint_doi:
            doi = preprint_doi

    is_arxiv = False
    if not doi:
        arxiv_id = arxiv_id or find_arxiv_id(search_text, source_hint)
        if arxiv_id:
            is_arxiv = True
            meta = fetch_arxiv(arxiv_id)
            # If the preprint was published in a journal, prefer that richer
            # record but keep the arXiv OA PDF and abs URL as fallbacks.
            if meta and meta.journal_doi:
                published = _resolve_by_doi(meta.journal_doi)
                if published:
                    published.oa_pdf_url = published.oa_pdf_url or meta.oa_pdf_url
                    published.abstract = published.abstract or meta.abstract
                    if not published.authors:
                        published.authors = meta.authors
                    meta = published

    # Only fall back to text-scraped DOIs when this is NOT a known arXiv preprint
    # — on a preprint the first DOI in the body is a cited reference's, which is
    # exactly the bug this branch exists to avoid.
    if meta is None and not is_arxiv:
        doi = doi or find_doi(search_text)
        if doi:
            meta = _resolve_by_doi(doi)

    if meta is None:
        # For an arXiv preprint whose API lookup failed, keep the arXiv abs URL.
        fallback_url = (
            f"https://arxiv.org/abs/{arxiv_id}" if is_arxiv and arxiv_id
            else (f"https://doi.org/{doi}" if doi else "")
        )
        meta = PaperMeta(
            doi="" if is_arxiv else doi,
            url=fallback_url,
            is_preprint=is_arxiv,
        )
        # A detected medRxiv/bioRxiv self-DOI that no registry resolved yet (e.g. a
        # brand-new prefix): keep the correct identity rather than fall through to a
        # wrong-paper guess. Venue from the banner, year from the DOI's date suffix.
        if preprint_doi and not is_arxiv:
            meta.is_preprint = True
            meta.venue = "medRxiv" if preprint_server == "medrxiv" else "bioRxiv"
            ym = re.search(r"/(\d{4})\.\d{2}\.\d{2}", preprint_doi)
            if ym:
                meta.year = ym.group(1)
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
