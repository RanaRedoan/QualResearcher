"""ffmpeg helpers: read duration and cut long recordings into chunks at pauses.

Cutting at silences (instead of every N seconds exactly) avoids splitting a word
in half, which is where AI transcripts usually lose text.
"""
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


class AudioError(Exception):
    pass


@dataclass
class Chunk:
    path: Path
    offset: float  # seconds from the start of the original recording
    duration: float


# In the windowed desktop app every ffmpeg call would otherwise flash a black console window.
_NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def _run(cmd, timeout=None):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, **_NO_WINDOW)


def probe_duration(path) -> float:
    """Return duration in seconds, or raise AudioError if ffprobe cannot read the file."""
    result = _run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        timeout=120,
    )
    try:
        duration = float(json.loads(result.stdout)["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError, TypeError):
        raise AudioError("We could not read this file as audio. Please upload MP3, WAV, M4A, OGG or MP4.")
    if duration <= 0.5:
        raise AudioError("This recording looks empty (shorter than one second).")
    return duration


_SILENCE_RE = re.compile(r"silence_(start|end): (-?[\d.]+)")


def detect_silences(path, noise_db=-35, min_silence=0.4):
    """Return a list of (start, end) silent stretches."""
    result = _run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn",
         "-af", f"silencedetect=noise={noise_db}dB:d={min_silence}", "-f", "null", "-"],
        timeout=1800,
    )
    silences, start = [], None
    for kind, value in _SILENCE_RE.findall(result.stderr):
        value = float(value)
        if kind == "start":
            start = value
        elif start is not None:
            silences.append((start, value))
            start = None
    return silences


def plan_cut_points(duration, chunk_seconds, silences, search_window=60):
    """Choose cut times near every `chunk_seconds`, snapped to the middle of a pause.

    Returns a sorted list of cut times (not including 0 or `duration`).
    """
    window = min(search_window, chunk_seconds / 4)
    cuts, target = [], float(chunk_seconds)
    while target < duration - chunk_seconds * 0.25:
        lo, hi = target - window, target + window
        candidates = [((s + e) / 2, e - s) for s, e in silences if lo <= (s + e) / 2 <= hi]
        if candidates:
            # prefer longer pauses (capped at 2 s), then closeness to the target
            cut = max(candidates, key=lambda c: (min(c[1], 2.0), -abs(c[0] - target)))[0]
        else:
            cut = target
        cuts.append(round(cut, 2))
        target = cut + chunk_seconds  # always moves forward by at least 3/4 of a chunk
    return cuts


def make_chunks(src, workdir, chunk_seconds=600, duration=None) -> list[Chunk]:
    """Convert to small mono MP3 pieces (16 kHz, 48 kbps — about 3.6 MB per 10 min)."""
    src, workdir = Path(src), Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    duration = duration or probe_duration(src)

    cuts = []
    if duration > chunk_seconds * 1.25:
        cuts = plan_cut_points(duration, chunk_seconds, detect_silences(src))
    bounds = [0.0, *cuts, duration]

    chunks = []
    for i, (start, end) in enumerate(zip(bounds[:-1], bounds[1:])):
        out = workdir / f"chunk_{i:03d}.mp3"
        result = _run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-ss", f"{start:.2f}", "-i", str(src), "-t", f"{end - start:.2f}",
             "-vn", "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "48k", str(out)],
            timeout=1800,
        )
        if result.returncode != 0 or not out.exists():
            raise AudioError(f"ffmpeg could not process the audio: {result.stderr[-300:]}")
        chunks.append(Chunk(path=out, offset=start, duration=end - start))
    return chunks
