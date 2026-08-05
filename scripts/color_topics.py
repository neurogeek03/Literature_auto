#!/usr/bin/env python3
"""Generate Obsidian graph-view color groups for the user's topic taxonomy.

Deterministic: each Tier-1 family gets a base hue; the parent hub is a dark shade
of that hue and each Tier-2 child is a progressively lighter shade of the SAME hue.
Papers are left at the default node color so multi-topic papers stay unambiguous.

The taxonomy is read from config.yaml (`topics:`), the single source of truth
also used for the topic stubs and the skill vocabulary (see scripts/setup_topics.py).

Only the `colorGroups` key of the vault's .obsidian/graph.json is rewritten; every
other graph setting is preserved. A timestamped backup is written first.

Usage:
    python scripts/color_topics.py [--vault /path/to/vault] [--dry-run]

Re-running is safe and idempotent for a fixed vocabulary.
"""
from __future__ import annotations

import argparse
import colorsys
import datetime as _dt
import json
import shutil
import sys
from pathlib import Path

# Allow running as a standalone script (`python scripts/color_topics.py`).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import topics_taxonomy, vault_dir  # noqa: E402

# Shade ramp: parent is darkest; children step lighter. Saturation held constant
# so every node in a family reads as "the same color, different shade".
PARENT_LIGHTNESS = 0.40
CHILD_LIGHTNESS_START = 0.55
CHILD_LIGHTNESS_END = 0.72
SATURATION = 0.65


def hsl_to_obsidian_rgb(h: float, s: float, l: float) -> int:
    """HSL (0-1) -> 24-bit int (r<<16 | g<<8 | b), the format graph.json stores."""
    r, g, b = colorsys.hls_to_rgb(h, l, s)  # note: colorsys uses HLS order
    ri, gi, bi = (round(r * 255), round(g * 255), round(b * 255))
    return (ri << 16) | (gi << 8) | bi


def build_color_groups(families: dict[str, list[dict]]) -> list[dict]:
    """One base hue per family; the hub is darkest, children ramp lighter.

    `families` is the taxonomy from src.config.topics_taxonomy():
    family -> list of {"slug", "desc"} children (possibly empty).
    """
    groups: list[dict] = []
    n = max(len(families), 1)
    for i, (parent, children) in enumerate(families.items()):
        hue = i / n  # evenly spaced around the wheel

        # Parent hub: exact path match so it never collides with a child whose
        # name contains the parent (e.g. single-cell vs single-cell-biology).
        groups.append(
            {
                "query": f'path:"topics/{parent}.md"',
                "color": {"a": 1, "rgb": hsl_to_obsidian_rgb(hue, SATURATION, PARENT_LIGHTNESS)},
            }
        )

        # Children: same hue, lightness ramped so each reads as a distinct shade.
        for j, child in enumerate(children):
            slug = child["slug"]
            if len(children) == 1:
                light = (CHILD_LIGHTNESS_START + CHILD_LIGHTNESS_END) / 2
            else:
                t = j / (len(children) - 1)
                light = CHILD_LIGHTNESS_START + t * (CHILD_LIGHTNESS_END - CHILD_LIGHTNESS_START)
            groups.append(
                {
                    "query": f'path:"topics/{slug}.md"',
                    "color": {"a": 1, "rgb": hsl_to_obsidian_rgb(hue, SATURATION, light)},
                }
            )
    return groups


def write_color_groups(vault: Path, groups: list[dict], *, dry_run: bool = False) -> None:
    """Rewrite only colorGroups in the vault's graph.json (backup first)."""
    graph_path = vault / ".obsidian" / "graph.json"
    if not graph_path.exists():
        raise SystemExit(
            f"graph.json not found at {graph_path} (open the graph view once first)"
        )

    config = json.loads(graph_path.read_text())
    config["colorGroups"] = groups

    for g in groups:
        rgb = g["color"]["rgb"]
        print(f'  #{rgb:06x}  {g["query"]}')

    if dry_run:
        print("\n[dry-run] graph.json not modified.")
        return

    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = graph_path.with_suffix(f".json.bak-{stamp}")
    shutil.copy2(graph_path, backup)
    graph_path.write_text(json.dumps(config, indent=2))
    print(f"\nWrote {len(groups)} color groups to {graph_path}")
    print(f"Backup: {backup}")
    print("Reload the graph view (close/reopen) to see the colors.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", type=Path, default=None, help="override config vault.path")
    ap.add_argument("--dry-run", action="store_true", help="print groups, do not write")
    args = ap.parse_args()

    families = topics_taxonomy()
    if not families:
        raise SystemExit(
            "No themes defined. Run `python scripts/setup_topics.py` first to "
            "define your topics (config.yaml -> topics:)."
        )

    vault = args.vault or vault_dir()
    write_color_groups(vault, build_color_groups(families), dry_run=args.dry_run)


if __name__ == "__main__":
    main()
