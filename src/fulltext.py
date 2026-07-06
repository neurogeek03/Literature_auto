"""Deterministic PDF -> Markdown conversion (pymupdf4llm) + helpers."""
from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF
import pymupdf4llm


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
