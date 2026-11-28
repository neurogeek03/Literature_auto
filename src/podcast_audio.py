"""Voice a podcast script with local Kokoro (mlx-audio), line by line, with
short pauses between turns, then encode to MP3 with ffmpeg."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from .config import CONFIG

SR = 24000


def _pause_after(script: list[dict], i: int) -> float:
    """Seconds of silence after line i."""
    pc = CONFIG["podcast"]["pauses"]
    nxt = script[i + 1] if i + 1 < len(script) else None
    if nxt is None:
        return 0.0
    if nxt.get("segment"):
        return pc["segment"]
    if script[i]["text"].endswith("—"):
        return pc["interrupt"]  # cut-off line: answer comes fast
    if len(nxt["text"].split()) <= 3:
        return pc["backchannel"]
    if nxt["speaker"] == script[i]["speaker"]:
        return pc["same_speaker"]
    return pc["turn"]


def render_mp3(script: list[dict], out_mp3: Path) -> float:
    """Voice the script into out_mp3; returns duration in seconds."""
    import soundfile as sf
    from mlx_audio.tts.utils import load_model

    pc = CONFIG["podcast"]
    voices = {"A": pc["voices"]["A"], "B": pc["voices"]["B"]}
    model = load_model(pc.get("kokoro_model", "mlx-community/Kokoro-82M-bf16"))

    parts = []
    for i, line in enumerate(script):
        if line.get("segment"):
            continue
        for r in model.generate(text=line["text"], voice=voices[line["speaker"]], speed=1.0, lang_code="a"):
            parts.append(np.array(r.audio, dtype=np.float32))
        parts.append(np.zeros(int(_pause_after(script, i) * SR), dtype=np.float32))
    audio = np.concatenate(parts)

    ffmpeg = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "episode.wav"
        sf.write(wav, audio, SR)
        subprocess.run(
            [ffmpeg, "-loglevel", "error", "-y", "-i", str(wav), "-ac", "1", "-b:a", "96k", str(out_mp3)],
            check=True,
        )
    return len(audio) / SR
