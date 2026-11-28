"""Voice a podcast script with local Kokoro (mlx-audio), line by line, with
short pauses between turns, then encode to MP3 with ffmpeg.
MLX backend on Apple Silicon, PyTorch `kokoro` elsewhere (Linux, Windows, Intel Mac)."""
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
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


def use_mlx() -> bool:
    """MLX runs Kokoro fast on Apple Silicon; everything else uses PyTorch (CPU)."""
    return sys.platform == "darwin" and platform.machine() == "arm64"


def _voicer():
    """Returns say(text, voice) -> float32 audio at 24 kHz."""
    pc = CONFIG["podcast"]
    if use_mlx():
        from mlx_audio.tts.utils import load_model

        model = load_model(pc.get("kokoro_model", "mlx-community/Kokoro-82M-bf16"))
        return lambda text, voice: np.concatenate([
            np.array(r.audio, dtype=np.float32)
            for r in model.generate(text=text, voice=voice, speed=1.0, lang_code="a")
        ])

    from kokoro import KPipeline

    pipe = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M")
    return lambda text, voice: np.concatenate([
        np.asarray(audio, dtype=np.float32) for _, _, audio in pipe(text, voice=voice, speed=1.0)
    ])


def _ffmpeg() -> str:
    """System ffmpeg if installed, else the one bundled with imageio-ffmpeg."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def render_mp3(script: list[dict], out_mp3: Path) -> float:
    """Voice the script into out_mp3; returns duration in seconds."""
    import soundfile as sf

    pc = CONFIG["podcast"]
    voices = {"A": pc["voices"]["A"], "B": pc["voices"]["B"]}
    say = _voicer()

    parts = []
    for i, line in enumerate(script):
        if line.get("segment"):
            continue
        parts.append(say(line["text"], voices[line["speaker"]]))
        parts.append(np.zeros(int(_pause_after(script, i) * SR), dtype=np.float32))
    audio = np.concatenate(parts)

    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "episode.wav"
        sf.write(wav, audio, SR)
        subprocess.run(
            [_ffmpeg(), "-loglevel", "error", "-y", "-i", str(wav), "-ac", "1", "-b:a", "96k", str(out_mp3)],
            check=True,
        )
    return len(audio) / SR
