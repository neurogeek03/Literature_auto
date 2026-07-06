"""(Re)build the vault embedding index used for related-note discovery.

Runs incrementally inside the pipeline already; this CLI is for a manual full
refresh (e.g. after bulk-importing notes).

    uv run python -m src.build_index
"""
from __future__ import annotations

from . import related


def main() -> None:
    index = related.build_index()
    n = len(index.get("entries", {}))
    print(f"vault index: {n} notes embedded ({index.get('model')})")


if __name__ == "__main__":
    main()
