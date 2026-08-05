#!/usr/bin/env python3
"""Define your themes once, then materialize them everywhere.

The topic taxonomy is a two-tier map (family -> children) that lives in
`config.yaml` under `topics:` — the single source of truth. This script:

  1. Seeds that block if it is empty — either interactively (prompts you for your
     own top-level themes and optional sub-themes) or by importing an existing
     vault's `topics/*.md` stubs.
  2. Creates the topic stubs in your vault (`topics/<name>.md`).
  3. Regenerates the model-facing theme list inside both Claude Code skills
     (paper-node + poster-node), repo copies and installed copies.
  4. Colors the Obsidian graph: one base color per top-level theme, lighter
     shades of that color for its sub-themes.

Everything is additive and idempotent — re-run anytime to add more themes; nothing
is deleted.

Usage:
    python scripts/setup_topics.py                 # seed if empty, then sync all
    python scripts/setup_topics.py --add           # force the interactive prompt
    python scripts/setup_topics.py --import-vault   # seed from existing vault stubs
    python scripts/setup_topics.py --no-color       # skip the graph.json step
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Allow running as a standalone script.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import config as cfg  # noqa: E402
from scripts import color_topics  # noqa: E402

PLACEHOLDER_VAULT = "/ABSOLUTE/PATH/TO/YOUR/obsidian-vault"
CONFIG_PATH = ROOT / "config.yaml"

TOPICS_START = (
    "# === topics (managed by scripts/setup_topics.py — run that, or edit here + re-run) ==="
)
TOPICS_END = "# === end topics ==="

SKILL_START = "<!-- TOPICS:auto"  # opening marker prefix (rest of line varies)
SKILL_END = "<!-- /TOPICS -->"

# `conferences` is a special sub-topic namespace managed by src/conference.py;
# it is never part of the user taxonomy.
RESERVED = {"conferences"}


# --------------------------------------------------------------------------- #
# taxonomy <-> YAML block in config.yaml
# --------------------------------------------------------------------------- #

def _yaml_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def taxonomy_to_yaml(taxonomy: dict[str, list[dict]]) -> str:
    """Render the taxonomy as a readable, re-parseable `topics:` YAML block."""
    lines = [TOPICS_START, "topics:"]
    for family, children in taxonomy.items():
        if not children:
            lines.append(f"  {family}: []")
            continue
        lines.append(f"  {family}:")
        for child in children:
            desc = _yaml_escape(child.get("desc", ""))
            lines.append(f'    - {{slug: {child["slug"]}, desc: "{desc}"}}')
    lines.append(TOPICS_END)
    return "\n".join(lines) + "\n"


def write_topics_block(taxonomy: dict[str, list[dict]]) -> None:
    """Splice the managed topics block into config.yaml, preserving everything else."""
    if not CONFIG_PATH.exists():
        raise SystemExit(
            f"{CONFIG_PATH} not found. Run `cp config.example.yaml config.yaml` first."
        )
    text = CONFIG_PATH.read_text()
    block = taxonomy_to_yaml(taxonomy)
    if TOPICS_START in text and TOPICS_END in text:
        pattern = re.compile(
            re.escape(TOPICS_START) + r".*?" + re.escape(TOPICS_END) + r"\n?",
            re.DOTALL,
        )
        text = pattern.sub(block, text, count=1)
    else:
        if not text.endswith("\n"):
            text += "\n"
        text += "\n" + block
    CONFIG_PATH.write_text(text)
    print(f"Wrote topics block to {CONFIG_PATH}")


# --------------------------------------------------------------------------- #
# seeding: interactive prompt or import from an existing vault
# --------------------------------------------------------------------------- #

def prompt_taxonomy() -> dict[str, list[dict]]:
    print(
        "\nLet's define your themes. Each top-level theme gets its own color in the\n"
        "Obsidian graph; its sub-themes get shades of that color. You can add more\n"
        "anytime by re-running this script — nothing you enter now is final.\n"
    )
    taxonomy: dict[str, list[dict]] = {}
    try:
        while True:
            family = input("Top-level theme (blank to finish): ").strip()
            if not family:
                break
            family = _slugify(family)
            if family in RESERVED:
                print(f"  '{family}' is reserved; pick another name.")
                continue
            children: list[dict] = []
            print(
                f"  Sub-themes of '{family}' (blank to finish). Leave empty to use\n"
                f"  '{family}' itself as an assignable theme."
            )
            while True:
                sub = input(f"    sub-theme of {family} (blank to skip): ").strip()
                if not sub:
                    break
                slug = _slugify(sub)
                desc = input(f"      one-line description of '{slug}': ").strip()
                children.append({"slug": slug, "desc": desc})
            taxonomy[family] = children
    except EOFError:
        pass
    if not taxonomy:
        raise SystemExit("No themes entered; nothing to do.")
    return taxonomy


def import_from_vault(vault: Path) -> dict[str, list[dict]]:
    """Reconstruct the taxonomy from existing `topics/*.md` stubs.

    A stub with `Part of [[topics/<family>]]` is a child of that family; every
    other stub is a family (hub if it has children, else a childless leaf).
    """
    td = vault / (cfg.CONFIG["vault"].get("topics_subdir") or "topics")
    stubs = [p for p in td.glob("*.md") if p.stem not in RESERVED]
    if not stubs:
        raise SystemExit(f"No topic stubs found in {td} to import.")

    part_of = re.compile(r"Part of \[\[topics/([^\]]+)\]\]")
    child_of: dict[str, str] = {}
    for p in sorted(stubs):
        m = part_of.search(p.read_text())
        if m:
            child_of[p.stem] = m.group(1).strip()

    parents = set(child_of.values())
    taxonomy: dict[str, list[dict]] = {}
    for p in sorted(stubs):
        name = p.stem
        if name in child_of:
            continue  # placed under its parent below
        taxonomy.setdefault(name, [])
    for child, parent in sorted(child_of.items()):
        taxonomy.setdefault(parent, [])
        taxonomy[parent].append({"slug": child, "desc": ""})
    # A family referenced only as a parent (its hub stub may be missing) still
    # belongs in the map; the setdefault above handles that.
    _ = parents  # (kept for readability)
    print(
        f"Imported {len(taxonomy)} families / "
        f"{sum(len(c) for c in taxonomy.values())} sub-themes from {td}"
    )
    return taxonomy


def _slugify(s: str) -> str:
    s = s.strip().lower().replace(" ", "-").replace("_", "-")
    s = re.sub(r"[^a-z0-9-]", "", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s


# --------------------------------------------------------------------------- #
# materialize: vault stubs + skill vocab
# --------------------------------------------------------------------------- #

def materialize_stubs(vault: Path, taxonomy: dict[str, list[dict]]) -> int:
    """Create topics/<name>.md stubs (create-if-missing, never clobber)."""
    td = vault / (cfg.CONFIG["vault"].get("topics_subdir") or "topics")
    td.mkdir(parents=True, exist_ok=True)
    created = 0
    for family, children in taxonomy.items():
        hub = td / f"{family}.md"
        if not hub.exists():
            hub.write_text(f"> [!topic] {family}\n")
            created += 1
        for child in children:
            leaf = td / f"{child['slug']}.md"
            if not leaf.exists():
                leaf.write_text(
                    f"> [!topic] {child['slug']}\n> Part of [[topics/{family}]]\n"
                )
                created += 1
    return created


def _skill_vocab_block(leaves: list[dict], opening: str) -> str:
    lines = [opening]
    for leaf in leaves:
        desc = leaf.get("desc") or "(add a description in config.yaml -> topics)"
        lines.append(f"- `{leaf['slug']}` — {desc}")
    lines.append(SKILL_END)
    return "\n".join(lines)


def update_skill(path: Path, leaves: list[dict]) -> bool:
    """Rewrite the region between the TOPICS:auto markers in one SKILL.md."""
    if not path.exists():
        return False
    text = path.read_text()
    start = text.find(SKILL_START)
    end = text.find(SKILL_END)
    if start == -1 or end == -1 or end < start:
        print(
            f"  ! {path}: no TOPICS:auto markers found — reinstall this skill from "
            f"the repo `skills/` copy, then re-run."
        )
        return False
    opening_line = text[start : text.find("\n", start)]
    new_block = _skill_vocab_block(leaves, opening_line)
    new_text = text[:start] + new_block + text[end + len(SKILL_END) :]
    path.write_text(new_text)
    return True


def sync_skills(leaves: list[dict]) -> None:
    targets = [
        ROOT / "skills" / "paper-node" / "SKILL.md",
        ROOT / "skills" / "poster-node" / "SKILL.md",
        Path.home() / ".claude" / "skills" / "paper-node" / "SKILL.md",
        Path.home() / ".claude" / "skills" / "poster-node" / "SKILL.md",
    ]
    for t in targets:
        if update_skill(t, leaves):
            print(f"  synced vocab -> {t}")


# --------------------------------------------------------------------------- #

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--add", action="store_true", help="force the interactive prompt")
    ap.add_argument("--import-vault", action="store_true", help="seed from existing vault stubs")
    ap.add_argument("--no-color", action="store_true", help="skip the graph.json coloring step")
    args = ap.parse_args()

    vault = cfg.vault_dir()
    if str(vault) == PLACEHOLDER_VAULT or not str(vault).strip():
        raise SystemExit(
            "config.yaml -> vault.path is still the placeholder. Set it to your "
            "own Obsidian vault's absolute path, then re-run."
        )
    if not vault.exists():
        raise SystemExit(f"Vault path does not exist: {vault}")

    existing = cfg.topics_taxonomy()

    # Decide the taxonomy for this run.
    if args.add:
        new = prompt_taxonomy()
        taxonomy = {**existing, **new}  # additive; new families/leaves merge in
    elif args.import_vault or (not existing and _vault_has_stubs(vault)):
        taxonomy = import_from_vault(vault)
    elif not existing:
        taxonomy = prompt_taxonomy()
    else:
        taxonomy = existing  # already defined; just re-sync everything

    # Persist to config, then materialize from the in-memory taxonomy (avoids a
    # config reload). Build leaves the same way src.config.topic_leaves() does.
    write_topics_block(taxonomy)
    leaves = _leaves(taxonomy)

    created = materialize_stubs(vault, taxonomy)
    print(f"Topic stubs: {created} created (existing left untouched).")

    print("Syncing skill vocabulary:")
    sync_skills(leaves)

    if not args.no_color:
        graph = vault / ".obsidian" / "graph.json"
        if graph.exists():
            print("Coloring graph:")
            color_topics.write_color_groups(vault, color_topics.build_color_groups(taxonomy))
        else:
            print(
                f"  (skipped colors: {graph} not found — open the Obsidian graph "
                "view once, then run `python scripts/color_topics.py`)"
            )

    fam = len(taxonomy)
    n_leaves = len(leaves)
    print(
        f"\nDone: {fam} top-level theme(s), {n_leaves} assignable theme(s).\n"
        "Add more themes anytime with `python scripts/setup_topics.py --add` — "
        "stubs and colors are additive, nothing is deleted."
    )


def _vault_has_stubs(vault: Path) -> bool:
    td = vault / (cfg.CONFIG["vault"].get("topics_subdir") or "topics")
    return td.exists() and any(p.stem not in RESERVED for p in td.glob("*.md"))


def _leaves(taxonomy: dict[str, list[dict]]) -> list[dict]:
    leaves: list[dict] = []
    seen: set[str] = set()
    for family, children in taxonomy.items():
        if children:
            for child in children:
                if child["slug"] not in seen:
                    seen.add(child["slug"])
                    leaves.append(child)
        elif family not in seen:
            seen.add(family)
            leaves.append({"slug": family, "desc": ""})
    return leaves


if __name__ == "__main__":
    main()
