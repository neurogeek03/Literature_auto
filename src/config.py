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


def valid_topics() -> set[str]:
    """The vocabulary is the set of topic stub filenames in the vault."""
    td = topics_dir()
    if not td.exists():
        return set()
    return {f.stem for f in td.glob("*.md")}
