"""Load config.yaml + .env. Single source of paths/settings for the pipeline."""
from __future__ import annotations

import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _load_env(path: Path) -> None:
    """Minimal .env loader (no external dep). Does not override existing env vars."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


def resolve_path(p: str | os.PathLike) -> Path:
    """Absolute paths pass through; relative ones resolve against the repo root."""
    p = Path(p)
    return p if p.is_absolute() else (ROOT / p)


def load_config() -> dict:
    _load_env(ROOT / ".env")
    cfg_path = ROOT / "config.yaml"
    if not cfg_path.exists():
        cfg_path = ROOT / "config.example.yaml"
    cfg = yaml.safe_load(cfg_path.read_text()) or {}
    cfg["_root"] = str(ROOT)
    return cfg


# Loaded once on import; cheap.
CONFIG = load_config()


def vault_dir() -> Path:
    return Path(CONFIG["vault"]["path"])


def notes_dir() -> Path:
    sub = CONFIG["vault"].get("notes_subdir") or ""
    d = vault_dir() / sub if sub else vault_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def topics_dir() -> Path:
    return vault_dir() / (CONFIG["vault"].get("topics_subdir") or "topics")


def images_dir() -> Path:
    d = vault_dir() / (CONFIG["vault"].get("images_subdir") or "images")
    d.mkdir(parents=True, exist_ok=True)
    return d


def valid_topics() -> set[str]:
    """The vocabulary is the set of topic stub filenames in the vault."""
    td = topics_dir()
    if not td.exists():
        return set()
    return {f.stem for f in td.glob("*.md")}


def topics_taxonomy() -> dict[str, list[dict]]:
    """The user's two-tier theme taxonomy from config: family -> [child, ...].

    Each family key maps to a list of child leaves ({"slug", "desc"}). A family
    with an empty list is itself the single assignable leaf. Single source of
    truth for topic stubs, skill vocabulary, and graph colors; materialized by
    scripts/setup_topics.py. Empty dict if `topics:` is unset.
    """
    raw = CONFIG.get("topics") or {}
    taxonomy: dict[str, list[dict]] = {}
    for family, children in raw.items():
        norm: list[dict] = []
        for child in children or []:
            if isinstance(child, str):
                norm.append({"slug": child, "desc": ""})
            elif isinstance(child, dict) and child.get("slug"):
                norm.append({"slug": str(child["slug"]), "desc": str(child.get("desc") or "")})
        taxonomy[str(family)] = norm
    return taxonomy


def topic_leaves() -> list[dict]:
    """Flatten the taxonomy to assignable leaves ({"slug", "desc"}).

    Rule: every child slug is a leaf; a childless family is itself a leaf. This
    is the exact set the model is told to choose from (skill vocabulary).
    """
    leaves: list[dict] = []
    seen: set[str] = set()
    for family, children in topics_taxonomy().items():
        if children:
            for child in children:
                if child["slug"] not in seen:
                    seen.add(child["slug"])
                    leaves.append(child)
        else:
            if family not in seen:
                seen.add(family)
                leaves.append({"slug": family, "desc": ""})
    return leaves
