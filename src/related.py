"""Related-note discovery via local embeddings (fastembed) + cosine similarity.

Deterministic: same text -> same vector -> same ranking. The vault index is
cached and rebuilt incrementally (only new/changed notes are re-embedded).
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import numpy as np

from .config import CONFIG, notes_dir, resolve_path

_MODEL = None

_TITLE_RE = re.compile(r"\*\*Title\*\*::\s*(.+)")
_ABSTRACT_RE = re.compile(r"\[!Abstract\][^\n]*\n>\s?(.+)")
_NODE_RE = re.compile(r"%% node:start %%\n(.+?)%% node:end %%", re.DOTALL)


def _model():
    global _MODEL
    if _MODEL is None:
        from pathlib import Path

        from fastembed import TextEmbedding

        cache_dir = str(Path.home() / ".cache" / "fastembed")
        _MODEL = TextEmbedding(model_name=CONFIG["related"]["model"], cache_dir=cache_dir)
    return _MODEL


def embed(texts: list[str]) -> np.ndarray:
    vecs = list(_model().embed(texts))
    arr = np.array(vecs, dtype=np.float32)
    # L2-normalize so cosine == dot product.
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return arr / norms


def extract_embed_text(md: str) -> str:
    """Title + abstract from one of our literature notes (the semantic gist).
    Poster/slide notes have no abstract; fall back to the Key Points bullets."""
    title = ""
    mt = _TITLE_RE.search(md)
    if mt:
        title = mt.group(1).strip()
    abstract = ""
    ma = _ABSTRACT_RE.search(md)
    if ma:
        abstract = ma.group(1).strip()
    if not abstract:
        mn = _NODE_RE.search(md)
        if mn:
            abstract = re.sub(r"\s+", " ", mn.group(1)).strip()
    text = (title + "\n" + abstract).strip()
    return text or title


def _citekey_from_file(path: Path) -> str:
    return path.stem.lstrip("@")


def _index_path() -> Path:
    p = resolve_path(CONFIG["related"]["index_path"])
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _note_files() -> list[Path]:
    return sorted(notes_dir().glob("@*.md"))


def build_index() -> dict:
    """Incrementally (re)build the cached vault embedding index."""
    idx_path = _index_path()
    index: dict = {}
    if idx_path.exists():
        try:
            index = json.loads(idx_path.read_text())
        except Exception:
            index = {}

    entries: dict = index.get("entries", {})
    to_embed: list[tuple[str, str]] = []  # (citekey, text)

    current_keys = set()
    for f in _note_files():
        ck = _citekey_from_file(f)
        current_keys.add(ck)
        md = f.read_text(errors="ignore")
        text = extract_embed_text(md)
        h = hashlib.sha1(text.encode("utf-8")).hexdigest()
        cached = entries.get(ck)
        if not cached or cached.get("hash") != h:
            to_embed.append((ck, text))
            entries[ck] = {"hash": h, "text": text, "vector": None}

    # Drop deleted notes.
    for ck in list(entries.keys()):
        if ck not in current_keys:
            del entries[ck]

    if to_embed:
        vectors = embed([t for _, t in to_embed])
        for (ck, _), vec in zip(to_embed, vectors):
            entries[ck]["vector"] = vec.tolist()

    index = {"model": CONFIG["related"]["model"], "entries": entries}
    idx_path.write_text(json.dumps(index))
    return index


def find_similar_preprint(meta, query_text: str, min_sim: float = 0.95):
    """A preprint note that is almost certainly the SAME paper as ``meta`` (a
    published record) — the robust preprint->published match when the journal
    retitled the paper and Crossref hasn't linked it yet.

    Uses the cached embedding index: the preprint and published versions of one
    paper share near-identical abstracts, so cosine runs ~0.99 vs ~0.6 for
    unrelated work. Gated on a high threshold AND the same first-author last name
    AND pub_type == preprint, so it never collapses two genuinely different
    papers. Returns the note Path or None."""
    from . import note_render  # lazy: avoids any import cycle

    if not query_text or not getattr(meta, "authors", None):
        return None
    index = build_index()
    entries = index.get("entries", {})
    if not entries:
        return None

    qvec = embed([query_text])[0]
    ranked = sorted(
        ((ck, float(np.array(e["vector"], dtype=np.float32) @ qvec))
         for ck, e in entries.items() if e.get("vector")),
        key=lambda x: -x[1],
    )
    want_last = note_render._last_name(meta.authors[0])
    for ck, sim in ranked:
        if sim < min_sim:
            break
        p = notes_dir() / f"@{ck}.md"
        if note_render._fm_pub_type(p) != "preprint":
            continue
        txt = p.read_text(errors="ignore")
        ma = note_render._NOTE_AUTHORS_RE.search(txt)
        note_last = note_render._last_name(ma.group(1).split(";")[0]) if ma else ""
        if want_last and note_last and want_last == note_last:
            return p
    return None


def related(
    query_text: str,
    exclude_citekey: str = "",
    exclude_also: "set[str] | None" = None,
) -> list[str]:
    """Top-k citekeys most similar to query_text, above min_similarity.

    ``exclude_also`` drops extra citekeys from the candidate set — e.g. a paper's
    old preprint citekey during a preprint->published update, so the paper never
    relates to itself under its former name."""
    top_k = int(CONFIG["related"]["top_k"])
    min_sim = float(CONFIG["related"]["min_similarity"])
    excluded = {exclude_citekey} | (exclude_also or set())
    index = build_index()
    entries = index.get("entries", {})

    keys, mat = [], []
    for ck, e in entries.items():
        if ck in excluded or not e.get("vector"):
            continue
        keys.append(ck)
        mat.append(e["vector"])
    if not keys:
        return []

    qvec = embed([query_text])[0]
    sims = np.array(mat, dtype=np.float32) @ qvec
    order = np.argsort(-sims)
    out = []
    for i in order:
        if sims[i] < min_sim:
            break
        out.append(keys[i])
        if len(out) >= top_k:
            break
    return out
