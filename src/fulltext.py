"""Deterministic PDF -> Markdown conversion (pymupdf4llm) + helpers."""
from __future__ import annotations

import re
from pathlib import Path

import fitz  # PyMuPDF
import pymupdf4llm

_DOI_IN_XMP_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")


def first_pages_text(pdf_path: str | Path, n: int = 2) -> str:
    """Plain text of the first n pages — used for DOI detection."""
    text_parts = []
    with fitz.open(pdf_path) as doc:
        for i, page in enumerate(doc):
            if i >= n:
                break
            text_parts.append(page.get_text())
    return "\n".join(text_parts)


def to_markdown(pdf_path: str | Path) -> str:
    """Full paper as Markdown. Deterministic, CPU, no ML model."""
    return pymupdf4llm.to_markdown(str(pdf_path))


def word_count(text: str) -> int:
    return len(text.split())


def embedded_metadata(pdf_path: str | Path) -> dict:
    """PDF document-info + XMP metadata (title/author/subject + any embedded DOI).

    Used to recover identity when no DOI appears in the body text (e.g. old
    Elsevier/ScienceDirect PDFs that stamp the PII in the document title but print
    no DOI anywhere). Returns the ``fitz`` info dict (keys: ``title``, ``author``,
    ``subject``, ``keywords``, ...) plus a ``doi`` key scraped from the XMP packet
    when present. Best-effort: a malformed/unreadable PDF yields ``{}``.
    """
    try:
        with fitz.open(pdf_path) as doc:
            info = dict(doc.metadata or {})
            try:
                xmp = doc.get_xml_metadata() or ""
            except Exception:
                xmp = ""
        if xmp:
            m = _DOI_IN_XMP_RE.search(xmp)
            if m:
                info["doi"] = m.group(0).rstrip(").,;")
        return info
    except Exception:
        return {}
