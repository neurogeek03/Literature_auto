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


def related(query_text: str, exclude_citekey: str = "") -> list[str]:
    """Top-k citekeys most similar to query_text, above min_similarity."""
    top_k = int(CONFIG["related"]["top_k"])
    min_sim = float(CONFIG["related"]["min_similarity"])
    index = build_index()
    entries = index.get("entries", {})

    keys, mat = [], []
    for ck, e in entries.items():
        if ck == exclude_citekey or not e.get("vector"):
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
