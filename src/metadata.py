"""Deterministic metadata: DOI -> Crossref/OpenAlex -> clean fields + citekey.

No LLM. Same input -> same output.
"""
from __future__ import annotations

import difflib
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
    # Set by fetch_arxiv (arXiv <-> journal) or fetch_crossref (relation
    # is-preprint-of) when a preprint links its published journal version, so
    # get_metadata can prefer the richer Crossref/OpenAlex record.
    journal_doi: str = ""
    # The reverse link: set on a *published* record that points back at its
    # preprint (Crossref relation has-preprint, or an OpenAlex preprint
    # location). Lets the pipeline find and update the existing preprint note
    # instead of writing a duplicate. See note_render.find_note_by_doi.
    preprint_doi: str = ""


# Explicit "DOI: 10.xxx" label — marks the article's *own* DOI in end-matter,
# as opposed to the bare/"doi.org/" DOIs of cited references.
DOI_LABEL_RE = re.compile(r"(?i)\bdoi:\s*(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)")
# A DOI printed as a doi.org URL. In the page-1 header this is the article's OWN
# DOI (Elsevier/Cell/Nature stamp it there); deep in the body it's a cited ref.
DOI_URL_RE = re.compile(
    r"(?i)(?:https?://)?(?:dx\.)?doi\.org/(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)"
)
# Window at the start of the full text that counts as the article's own header.
DOI_HEAD_CHARS = 2500


def _clean_doi(doi: str) -> str:
    # Strip common trailing artifacts from PDF text extraction.
    doi = doi.rstrip(").,;")
    # Drop an accidental trailing 'pdf' glued on by some extractors.
    doi = re.sub(r"(?i)pdf$", "", doi).rstrip(").,;")
    return doi


def _valid_doi(doi: str) -> bool:
    """Reject markdown-truncated DOI fragments.

    Emphasis/heading markers in the Markdown conversion can clip a DOI at the
    first journal token, leaving fragments like ``10.1016/j`` (Elsevier) or
    ``10.48550/arXiv`` with no real suffix. These then fuzzy-match the WRONG paper
    on OpenAlex, so we drop any DOI whose suffix is trivial.
    """
    suffix = doi.split("/", 1)[1] if "/" in doi else ""
    if suffix.lower() in {"j", "arxiv"}:
        return False
    return len(suffix) >= 3


def find_doi(text: str) -> str:
    """The article's *own* DOI, not the first DOI-looking token.

    Journals like Science/Sci. Transl. Med./Sci. Adv. print the article DOI only
    in the end-matter, *after* the reference list — so the first DOI in the text
    is usually a cited reference's DOI. We score every DOI by occurrence count,
    discard markdown-truncated prefix fragments, add a strong bonus for the DOI
    printed as a ``doi.org/`` URL in the page-1 header (the article's own) or next
    to an explicit "DOI:" label, and break ties by earliest position.
    """
    if not text:
        return ""
    score: dict[str, int] = {}
    first_pos: dict[str, int] = {}
    for m in DOI_RE.finditer(text):
        doi = _clean_doi(m.group(0))
        if not doi or not _valid_doi(doi):
            continue
        score[doi] = score.get(doi, 0) + 1
        first_pos.setdefault(doi, m.start())
    if not score:
        return ""
    # Drop prefix fragments: a candidate that is a strict prefix of another is a
    # truncated piece of the longer DOI (e.g. "10.1016/j.cell" of the full DOI),
    # never a distinct identifier.
    cands = list(score)
    for d in [d for d in cands if any(o != d and o.startswith(d) for o in cands)]:
        del score[d]
    if not score:
        return ""
    # The article's own DOI is stamped as a doi.org URL in the page-1 header.
    for m in DOI_URL_RE.finditer(text[:DOI_HEAD_CHARS]):
        doi = _clean_doi(m.group(1))
        if doi in score:
            score[doi] += 10
    for m in DOI_LABEL_RE.finditer(text):
        doi = _clean_doi(m.group(1))
        if doi in score:
            score[doi] += 5
    return max(score, key=lambda d: (score[d], -first_pos[d]))


# Elsevier/ScienceDirect PII (SICI-style), e.g. "S0166-4328(01)00170-X". Old
# Elsevier PDFs stamp this in the document title / page footer but print no DOI
# anywhere; the DOI is deterministically "10.1016/<PII>".
_ELSEVIER_PII_RE = re.compile(
    r"S\d{4}-?\d{3}[\dX]\s*\(\s*\d{2}\s*\)\s*\d{4,5}-?[\dX]", re.IGNORECASE
)


def find_elsevier_doi(*texts: str) -> str:
    """Recover an Elsevier DOI from a PII stamped in the given strings.

    Scans each string (typically the embedded PDF title and the first-page text)
    for an Elsevier PII and returns ``10.1016/<PII>`` (normalized, uppercase,
    internal whitespace removed), or ``""`` if none is found. Deterministic, no
    network.
    """
    for text in texts:
        if not text:
            continue
        m = _ELSEVIER_PII_RE.search(text)
        if m:
            pii = re.sub(r"\s+", "", m.group(0)).upper()
            return f"10.1016/{pii}"
    return ""


# Lines that are page furniture, not the article title: journal running headers
# ("Behavioural Brain Research 122 (2001) 1-9"), PII/DOI/URL stamps, and short
# section/editorial labels.
_RUNNING_HEADER_RE = re.compile(r"\b\d{1,4}\s*\(\s*\d{4}\s*\)\s*\d")
_TITLE_NOISE_RE = re.compile(
    r"(?i)^(pii\b|doi\b|https?://|www\.|received\b|accepted\b|revised\b"
    r"|research\s+(report|article)|(short\s+)?communication|review\s+article"
    r"|available\s+online|contents\s+lists|journal\s+homepage)"
)
# Lines that mark the END of the title block (byline / affiliation / abstract):
# a corresponding-author asterisk, an author initials pattern ("A.M. Galea"), an
# affiliation keyword, an e-mail, or the abstract/keywords heading.
_TITLE_END_RE = re.compile(
    r"(?i)(\*|@|\b[A-Z]\.\s?[A-Z]?\.?\s?[A-Z][a-z]+"
    r"|\b(department|universi|institut|laborator|abstract|keywords|introduction)\b)"
)


def guess_title_from_text(text: str) -> str:
    """Best-effort article title from the first plausible run of PDF text lines.

    Skips running headers (journal volume/year/page lines), PII/DOI/URL stamps,
    and short section labels, so an old Elsevier running header is not mistaken for
    the title. Joins a title that wraps across consecutive lines, stopping at the
    byline/affiliation/abstract. Returns ``""`` if nothing plausible is found.
    """
    parts: list[str] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not parts:
            if len(line) <= 15:
                continue
            if _TITLE_NOISE_RE.match(line) or _RUNNING_HEADER_RE.search(line):
                continue
            parts.append(line)
            continue
        # Collecting the (possibly multi-line) title; stop at the byline onwards.
        if not line or _TITLE_END_RE.search(line) or len(" ".join(parts)) > 250:
            break
        parts.append(line)
    return " ".join(parts).strip()


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


def _relation_doi(relation: dict, key: str) -> str:
    """First DOI-typed id under a Crossref ``relation`` key (has-preprint /
    is-preprint-of), or ''. Crossref lists these as
    ``{"id-type": "doi", "id": "10.x/y"}``."""
    for item in (relation or {}).get(key, []) or []:
        if (item.get("id-type") or "").lower() == "doi" and item.get("id"):
            return _clean_doi(item["id"].strip())
    return ""


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
    relation = m.get("relation") or {}
    return PaperMeta(
        title=title,
        authors=_clean_authors(authors),
        year=year,
        venue=venue,
        abstract=abstract,
        doi=doi,
        url=m.get("URL", f"https://doi.org/{doi}"),
        is_preprint=is_preprint,
        journal_doi=_relation_doi(relation, "is-preprint-of"),
        preprint_doi=_relation_doi(relation, "has-preprint"),
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
    is_preprint = w.get("type") == "preprint"
    # On a *published* work, look for a preprint-server location so we can point
    # back at the preprint DOI (secondary to Crossref's relation.has-preprint).
    preprint_doi = ""
    if not is_preprint:
        preprint_doi = _openalex_preprint_doi(w)
    return PaperMeta(
        title=_strip_tags(w.get("title") or ""),
        authors=_clean_authors(authors),
        year=str(w.get("publication_year") or ""),
        venue=((w.get("primary_location") or {}).get("source") or {}).get("display_name", "") or "",
        abstract=_openalex_abstract(w.get("abstract_inverted_index")),
        doi=doi,
        url=w.get("id", f"https://doi.org/{doi}"),
        oa_pdf_url=(oa.get("pdf_url") or "") if isinstance(oa, dict) else "",
        is_preprint=is_preprint,
        preprint_doi=preprint_doi,
    )


def _openalex_preprint_doi(w: dict) -> str:
    """Best-effort: a preprint DOI from an OpenAlex work's locations. Scans each
    location whose source looks like a preprint server for a DOI in its landing
    page / pdf URL. Returns '' when nothing preprint-like is linked."""
    for loc in w.get("locations", []) or []:
        source = (loc.get("source") or {}).get("display_name", "") or ""
        if not any(v in source.lower() for v in PREPRINT_VENUES):
            continue
        for url in (loc.get("landing_page_url"), loc.get("pdf_url")):
            if not url:
                continue
            m = DOI_RE.search(url)
            if m:
                return _clean_doi(m.group(0))
    return ""


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
        # Prefer Crossref's relation links, backfill from OpenAlex locations.
        meta.preprint_doi = meta.preprint_doi or oa.preprint_doi
        meta.journal_doi = meta.journal_doi or oa.journal_doi
    elif oa and not meta:
        meta = oa
    # Preprints (biorxiv/medrxiv) are often absent from Crossref/OpenAlex.
    if not meta:
        meta = fetch_biorxiv(doi)
    if meta and not meta.oa_pdf_url:
        meta.oa_pdf_url = fetch_unpaywall_pdf(doi)
    return meta


def _norm_title(title: str) -> str:
    """Lowercase, strip tags/punctuation, collapse whitespace — for comparison."""
    title = TAG_RE.sub(" ", title or "")
    title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    title = re.sub(r"[^a-z0-9 ]+", " ", title.lower())
    return re.sub(r"\s+", " ", title).strip()


def resolve_by_title(title: str, first_author_hint: str = "") -> PaperMeta | None:
    """Recover a record via a Crossref bibliographic title search.

    Last-resort identity recovery for a PDF with no DOI in its text or embedded
    metadata. Queries Crossref ``query.bibliographic``, then accepts the top hit
    ONLY when its normalized title closely matches ``title`` (SequenceMatcher ratio
    >= 0.9) and — when ``first_author_hint`` (a surname) is given — that surname
    appears among the hit's authors. On a confident match, re-resolves the matched
    DOI via :func:`_resolve_by_doi` for a full, consistent record. Returns ``None``
    when nothing matches confidently, so a genuinely unpublished PDF is never
    mislabelled.
    """
    title = (title or "").strip()
    if len(title) < 15:
        return None
    try:
        params = {"query.bibliographic": title, "rows": 5}
        if _mailto():
            params["mailto"] = _mailto()
        r = requests.get("https://api.crossref.org/works", params=params, timeout=20)
        r.raise_for_status()
        items = r.json()["message"]["items"]
    except Exception:
        return None

    want = _norm_title(title)
    want_tokens = set(want.split())
    want_surname = _ascii_last_name(first_author_hint).lower() if first_author_hint else ""
    for it in items:
        cand = _norm_title(" ".join(it.get("title") or []))
        if not cand:
            continue
        ratio_ok = difflib.SequenceMatcher(None, want, cand).ratio() >= 0.9
        # A wrapped/truncated PDF title is a token-subset of the real title; accept
        # that too (guarded below by the author-surname check when available).
        subset_ok = len(want_tokens) >= 5 and want_tokens <= set(cand.split())
        if not (ratio_ok or subset_ok):
            continue
        if want_surname:
            surnames = {
                _ascii_last_name(a.get("family", "")).lower()
                for a in (it.get("author") or [])
                if a.get("family")
            }
            if want_surname not in surnames:
                continue
        doi = (it.get("DOI") or "").strip()
        if doi:
            return _resolve_by_doi(doi) or fetch_crossref(doi)
    return None


def get_metadata(
    doi: str = "",
    pdf_text: str = "",
    doi_text: str | None = None,
    arxiv_id: str = "",
    source_hint: str = "",
    embedded_title: str = "",
    embedded_doi: str = "",
) -> PaperMeta:
    """Resolve metadata. Prefers Crossref, backfills from OpenAlex, falls back
    to a title guessed from the PDF text when no DOI is available.

    ``pdf_text`` (the first pages) drives the title fallback; ``doi_text`` is the
    text searched for a DOI — pass the *full* document so a DOI that only appears
    in the end-matter (Science journals) is found. Defaults to ``pdf_text``.
    ``embedded_title``/``embedded_doi`` come from the PDF's document-info/XMP
    metadata (see ``fulltext.embedded_metadata``) and drive DOI recovery for PDFs
    that print no DOI in the body (old Elsevier/ScienceDirect articles).

    Precedence: an explicit ``doi`` (user was deliberate) wins; else a
    medRxiv/bioRxiv self-DOI read from the page banner (authoritative — a cited
    reference can't spoof it, and it wins over any arXiv reference the preprint
    happens to cite); else an arXiv ID (``arxiv_id`` or one detected in the header
    region / ``source_hint``) resolves against the arXiv API — never via
    ``find_doi``, which would grab a cited reference's DOI on a preprint; else the
    normal ``find_doi``/Crossref flow; else, when still unresolved, recovery via an
    embedded-XMP DOI, an Elsevier PII, and finally a Crossref title search.
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

    # The publisher's own embedded (XMP) DOI is authoritative — prefer it over a
    # body-text scrape, which can grab a cited reference's DOI or a markdown-
    # truncated fragment (e.g. "10.1016/j" that then fuzzy-matches the wrong paper
    # on OpenAlex). Not for arXiv preprints, whose identity is the arXiv id.
    if meta is None and not is_arxiv and embedded_doi:
        resolved = _resolve_by_doi(embedded_doi)
        if resolved:
            doi, meta = embedded_doi, resolved

    # Else fall back to a text-scraped DOI. Only when NOT a known arXiv preprint —
    # on a preprint the first DOI in the body is a cited reference's, which is
    # exactly the bug this branch exists to avoid.
    if meta is None and not is_arxiv:
        scraped = doi or find_doi(search_text)
        if scraped:
            resolved = _resolve_by_doi(scraped)
            if resolved:
                doi, meta = scraped, resolved

    # Recovery for PDFs with no DOI in text or XMP (old Elsevier/ScienceDirect):
    # an Elsevier PII -> 10.1016/<PII>, then a Crossref title search. Only on the
    # non-arXiv path; neither can be hijacked by a cited reference (the PII is the
    # paper's own; the title query uses the title).
    title_guess = ""
    if meta is None and not is_arxiv:
        pii_doi = find_elsevier_doi(embedded_title, pdf_text)
        if pii_doi:
            doi = pii_doi
            meta = _resolve_by_doi(pii_doi)
        if meta is None:
            title_guess = guess_title_from_text(pdf_text) or embedded_title
            meta = resolve_by_title(title_guess)

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
        # Best-effort title: a plausible line of PDF text (skipping running
        # headers / PII stamps), the earlier title guess, or the embedded title.
        meta.title = (
            title_guess or guess_title_from_text(pdf_text) or embedded_title.strip()
        )

    # Venue-name fallback: catch preprints that lack a typed field.
    if not meta.is_preprint and meta.venue:
        meta.is_preprint = any(v in meta.venue.lower() for v in PREPRINT_VENUES)

    meta.citekey = make_citekey(meta)
    return meta
