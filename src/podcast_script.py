"""Podcast script: three headless `claude -p "/paper-podcast"` calls
(outline -> draft -> revise), then a deterministic check for lines the voice
will read badly, with one fix call if needed. Same pattern as node.py.
"""
from __future__ import annotations

import json
import re
import subprocess

from .config import CONFIG, resolve_path

_MAX_CHARS = 60_000
_BAD_LINE = re.compile(r"[0-9%±×→~/=<>&#*_\[\]{}|]")


def _usable(phase: str, out: str) -> bool:
    """Did the phase return what it should (not an error or a refusal note)?"""
    if phase == "outline":
        return "OUTLINE" in out
    if phase == "fix":
        return bool(parse_jsonl(out))
    return len(parse_jsonl(out)) >= 20


def _claude(phase: str, body: str) -> str:
    pc = CONFIG["podcast"]
    prompt = f"/{pc.get('skill', 'paper-podcast')}\n\nPHASE: {phase}\n\n{body}"
    d = resolve_path(pc.get("work_dir", ".cache/podcasts"))
    d.mkdir(parents=True, exist_ok=True)
    # Opus safeguards can false-flag biology papers; then try the next model.
    for model in [pc["model"]] + pc.get("fallback_models", []):
        proc = subprocess.run(
            [CONFIG["node"]["claude_bin"], "-p", "--model", model],
            input=prompt, capture_output=True, text=True,
            timeout=int(pc.get("timeout_seconds", 1200)),
        )
        out = proc.stdout or proc.stderr
        (d / f"last_{phase}.txt").write_text(f"[{model}]\n{out}")  # for debugging
        if proc.returncode == 0 and _usable(phase, proc.stdout):
            return proc.stdout
    raise RuntimeError(f"claude ({phase}) gave no usable output: {out.strip()[:300]}")


def parse_jsonl(text: str) -> list[dict]:
    """Keep the JSON objects, skip anything else the model printed."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def bad_lines(script: list[dict]) -> list[int]:
    """Indexes of lines the voice will read badly (digits, symbols, too long)."""
    return [
        i for i, l in enumerate(script)
        if "text" in l and (_BAD_LINE.search(l["text"]) or len(l["text"]) > 200)
    ]


def apply_fixes(script: list[dict], fixes: list[dict]) -> list[dict]:
    """Replace fixed lines; ' | ' splits one line into several (same speaker)."""
    by_i = {f["i"]: f["text"] for f in fixes if "i" in f and "text" in f}
    out = []
    for i, l in enumerate(script):
        if i in by_i:
            out += [{"speaker": l["speaker"], "text": t.strip()} for t in by_i[i].split(" | ") if t.strip()]
        else:
            out.append(l)
    return out


def write_script(fulltext: str, title: str, minutes: int = 25,
                 fmt: str = "deep_dive", focus: str = "") -> list[dict]:
    head = (
        f"Format: {fmt}\nTarget length: {minutes} minutes (about {minutes * 150} spoken words)\n"
        f"Paper title: {title}\n\n"
        + (f"<focus>\n{focus}\n</focus>\n\n" if focus else "")
        + f"<paper>\n{fulltext[:_MAX_CHARS]}\n</paper>\n"
    )
    outline = _claude("outline", head)
    draft = _claude("draft", head + f"\n<outline>\n{outline}\n</outline>\n")
    # Models underwrite long scripts: tell the revise pass how far off the draft is.
    words = sum(len(l.get("text", "").split()) for l in parse_jsonl(draft))
    target = minutes * 150
    note = (f"\nThe draft has {words} words; the target is {target}. Expand it to about {target} words "
            "with substance (methods, examples, limitations, back-and-forth), never padding.\n"
            if words < 0.9 * target else "")
    script = parse_jsonl(_claude("revise", head + note + f"\n<draft>\n{draft}\n</draft>\n"))
    script = [l for l in script if l.get("segment") or (l.get("speaker") in ("A", "B") and l.get("text"))]

    bad = bad_lines(script)
    if bad:
        listing = "\n".join(json.dumps({"i": i, "text": script[i]["text"]}) for i in bad)
        script = apply_fixes(script, parse_jsonl(_claude("fix", listing)))
    return script
