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

from . import codelinks, fulltext, metadata, note_render
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


def process_pdf(pdf_path: str | Path, doi: str = "") -> Result:
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        return Result(status="error", message=f"PDF not found: {pdf_path}")

    # 1. DOI + metadata
    try:
        head_text = fulltext.first_pages_text(pdf_path, n=2)
    except Exception as e:
        return Result(status="error", message=f"could not open PDF: {e}")
    meta = metadata.get_metadata(doi=doi, pdf_text=head_text)

    # 2. Full text + sufficiency gate
    try:
        ft = fulltext.to_markdown(pdf_path)
    except Exception as e:
        return Result(status="error", message=f"PDF->Markdown failed: {e}", meta=meta)
    wc = fulltext.word_count(ft)
    min_words = int(CONFIG["fulltext"]["min_words"])
    if wc < min_words:
        reason = SCANNED if wc < 50 else ABSTRACT_ONLY
        return Result(
            status="insufficient",
            reason=reason,
            word_count=wc,
            meta=meta,
            message=_insufficient_message(reason, meta.title, wc),
        )

    # 3. Code availability
    code = codelinks.find_code_links(ft)

    # 4. Related notes (deterministic)
    from . import related as related_mod  # lazy: loads fastembed model on demand
    related_keys = related_mod.related(
        _related_query(meta, ft), exclude_citekey=meta.citekey
    )

    # 5. Node (Claude Code; non-fatal on failure)
    node = run_node(ft, valid_topics())
    if not node.ok and not node.bullets:
        node = NodeResult(bullets="", topics=node.topics, error=node.error)

    # 6. Write note
    path = note_render.write_note(meta, ft, node, related_keys, code)

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
    )


def _main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Process one paper PDF -> Obsidian note.")
    ap.add_argument("pdf")
    ap.add_argument("--doi", default="")
    args = ap.parse_args(argv)

    r = process_pdf(args.pdf, doi=args.doi)
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
