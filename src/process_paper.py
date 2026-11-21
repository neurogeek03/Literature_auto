"""Orchestrator: PDF -> metadata -> full text (+ sufficiency gate) -> code ->
related -> node -> note. Deterministic except the node step.

CLI (offline, no Slack):
    uv run python -m src.process_paper /path/to/paper.pdf [--doi 10.xxx/yyy]
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import codelinks, conference, fulltext, metadata, note_render
from .config import CONFIG, valid_topics
from .metadata import PaperMeta
from .node import NodeResult, run_node

# Sufficiency-gate outcomes.
SCANNED = "scanned"
ABSTRACT_ONLY = "abstract_only"


@dataclass
class Result:
    status: str                       # "ok" | "insufficient" | "error"
    message: str = ""                 # human/Slack-facing text
    reason: str = ""                  # gate reason code when insufficient
    note_path: str = ""
    meta: PaperMeta | None = None
    related: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    code: list[str] = field(default_factory=list)
    word_count: int = 0
    node_error: str = ""
    conference: str = ""
    updated_from: str = ""            # old citekey when this re-dropped a now-published preprint


def _related_query(meta: PaperMeta, fulltext_md: str) -> str:
    base = (meta.title + "\n" + meta.abstract).strip()
    return base if len(base) > 40 else fulltext_md[:4000]


def _insufficient_message(reason: str, title: str, wc: int) -> str:
    if title and len(title) > 80:
        title = title[:77].rstrip() + "…"
    name = f"*{title}*" if title else "that file"
    if reason == SCANNED:
        return (f"{name} looks like a scanned/image PDF with no text layer — "
                "I can't read it. Send a text PDF.")
    return (f"That looks like just the abstract of {name} — I only pulled ~{wc} "
            "words. Send me the full-text PDF and I'll do the rest.")


def _update_published_note(existing, meta, ft, wc, min_words, focus_hint, conf_slug) -> Result:
    """Update an existing preprint note for its now-published version. Full
    refresh (new node + full text) when the published full text is sufficient;
    otherwise a metadata-only flip that keeps the preprint's content."""
    old_ck = existing.stem.lstrip("@")
    sufficient = wc >= min_words
    try:
        if sufficient:
            code = codelinks.find_code_links(ft)
            from . import related as related_mod  # lazy: loads fastembed on demand
            related_keys = related_mod.related(
                _related_query(meta, ft),
                exclude_citekey=meta.citekey,
                exclude_also={old_ck},
            )
            node = run_node(ft, valid_topics(), focus_hint=focus_hint)
            if not node.ok and not node.bullets:
                node = NodeResult(bullets="", topics=node.topics, error=node.error)
            path = note_render.update_published(
                existing, meta, ft, node, related_keys, code,
                conference=conf_slug, metadata_only=False,
            )
            topics, node_error = node.topics, node.error
        else:
            # Fail-safe: published PDF not accessible — keep the preprint's node /
            # full text, flip only the metadata (pub_type, venue, year, DOI).
            code, related_keys, topics, node_error = [], [], [], ""
            path = note_render.update_published(
                existing, meta, "", NodeResult(bullets="", topics=[]), [], [],
                metadata_only=True,
            )
        # Prune the stale preprint-citekey entry / register the new one.
        from . import related as related_mod
        related_mod.build_index()
    except Exception as e:
        return Result(status="error", message=f"failed while updating the note: {e}", meta=meta)

    new_ck = path.stem.lstrip("@")
    rename = f"@{old_ck} -> @{new_ck}" if new_ck != old_ck else f"@{new_ck}"
    if sufficient:
        message = f"Updated {rename} (now peer-reviewed: {meta.venue})".rstrip(": ")
    else:
        message = (f"Updated {rename} to peer-reviewed "
                   "(kept preprint full text — published PDF not accessible)")
    return Result(
        status="ok",
        message=message,
        note_path=str(path),
        meta=meta,
        related=related_keys,
        topics=topics,
        code=code,
        word_count=wc,
        node_error=node_error,
        conference=conf_slug,
        updated_from=old_ck,
    )


def process_pdf(
    pdf_path: str | Path, doi: str = "", prompt_text: str = "", arxiv_id: str = ""
) -> Result:
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        return Result(status="error", message=f"PDF not found: {pdf_path}")

    conf_slug, focus_hint = conference.parse_prompt(prompt_text)
    if conf_slug:
        conference.ensure_stub(conf_slug)

    # 1. Full text first — DOI discovery must see the whole document, since
    #    Science journals print the article's own DOI only in the end-matter.
    try:
        ft = fulltext.to_markdown(pdf_path)
    except Exception as e:
        return Result(status="error", message=f"PDF->Markdown failed: {e}")

    # 2. DOI + metadata. Title fallback uses the first pages; DOI search uses
    #    the full text.
    try:
        head_text = fulltext.first_pages_text(pdf_path, n=2)
    except Exception:
        head_text = ft[:4000]
    # Embedded PDF document-info/XMP metadata — recovers identity for PDFs that
    # print no DOI in the body (old Elsevier/ScienceDirect stamp the PII here).
    emb = fulltext.embedded_metadata(pdf_path)
    try:
        meta = metadata.get_metadata(
            doi=doi,
            pdf_text=head_text,
            doi_text=ft,
            arxiv_id=arxiv_id,
            source_hint=pdf_path.name,
            embedded_title=emb.get("title", ""),
            embedded_doi=emb.get("doi", ""),
        )
    except Exception as e:
        return Result(status="error", message=f"couldn't fetch metadata: {e}")

    wc = fulltext.word_count(ft)
    min_words = int(CONFIG["fulltext"]["min_words"])

    # 2b. Preprint -> published? If this published paper links back to a preprint
    # we already have a note for, update that note in place (rename + backlink
    # rewrite) instead of writing a duplicate. Checked BEFORE the sufficiency gate
    # so a paywalled published re-drop can still flip the note to peer-reviewed.
    existing = None
    if not meta.is_preprint:
        existing = (
            note_render.find_note_by_doi(meta.preprint_doi)
            or note_render.find_preprint_by_title(meta)
        )
        if existing is None:
            # Robust fallback: journals routinely retitle papers between preprint
            # and publication (and Crossref's has-preprint lags), so exact title /
            # relation matching misses. Embedding similarity over the vault index
            # catches the same paper under a new title (~0.99 cosine).
            from . import related as related_mod  # lazy: loads fastembed on demand
            existing = related_mod.find_similar_preprint(meta, _related_query(meta, ft))
    if existing is not None:
        return _update_published_note(existing, meta, ft, wc, min_words, focus_hint, conf_slug)

    # 3. Sufficiency gate
    if wc < min_words:
        reason = SCANNED if wc < 50 else ABSTRACT_ONLY
        return Result(
            status="insufficient",
            reason=reason,
            word_count=wc,
            meta=meta,
            message=_insufficient_message(reason, meta.title, wc),
        )

    # 3-6. Code links, related notes, node, and note write. The node step is
    # non-fatal by design (falls back to no bullets); everything else here
    # must surface as a Result rather than an unhandled exception, or a
    # permanent bug would silently retry forever under catchup.py.
    try:
        code = codelinks.find_code_links(ft)

        from . import related as related_mod  # lazy: loads fastembed model on demand
        related_keys = related_mod.related(
            _related_query(meta, ft), exclude_citekey=meta.citekey
        )

        node = run_node(ft, valid_topics(), focus_hint=focus_hint)
        if not node.ok and not node.bullets:
            node = NodeResult(bullets="", topics=node.topics, error=node.error)

        path = note_render.write_note(meta, ft, node, related_keys, code, conference=conf_slug)
    except Exception as e:
        return Result(status="error", message=f"failed while building the note: {e}", meta=meta)

    return Result(
        status="ok",
        message=f"Wrote {path.name}",
        note_path=str(path),
        meta=meta,
        related=related_keys,
        topics=node.topics,
        code=code,
        word_count=wc,
        node_error=node.error,
        conference=conf_slug,
    )


def _main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Process one paper PDF -> Obsidian note.")
    ap.add_argument("pdf")
    ap.add_argument("--doi", default="")
    ap.add_argument("--arxiv", default="", help="arXiv id (e.g. 2602.11632) to resolve authoritatively")
    ap.add_argument("--prompt", default="", help="caption text (conference tag + focus hint)")
    args = ap.parse_args(argv)

    r = process_pdf(args.pdf, doi=args.doi, prompt_text=args.prompt, arxiv_id=args.arxiv)
    print(f"status: {r.status}")
    if r.meta:
        print(f"title:  {r.meta.title}")
        print(f"citekey:{r.meta.citekey}  doi:{r.meta.doi}  year:{r.meta.year}")
    print(f"words:  {r.word_count}")
    if r.status == "ok":
        print(f"note:   {r.note_path}")
        print(f"topics: {r.topics}")
        print(f"related:{r.related}")
        print(f"code:   {r.code}")
        if r.node_error:
            print(f"node!:  {r.node_error}")
    else:
        print(f"message:{r.message}")
    return 0 if r.status == "ok" else 1


if __name__ == "__main__":
    sys.exit(_main())
