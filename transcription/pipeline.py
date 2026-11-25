"""Turn one uploaded recording into timestamped Bangla + English segments.

Steps: cut audio into ~10-minute pieces at pauses → transcribe each piece
(with the end of the previous piece as context) → shift timestamps to the
full-recording clock → translate the Bangla text in batches.
"""
import logging
import re
import shutil
import tempfile
from pathlib import Path

from django.conf import settings

from . import audio, gemini

log = logging.getLogger(__name__)

_TS_RE = re.compile(r"^\s*(?:(\d+):)?(\d{1,3}):(\d{1,2}(?:\.\d+)?)\s*$")


def parse_timestamp(value) -> float:
    """'MM:SS', 'HH:MM:SS' or a number of seconds → seconds (0.0 if unreadable)."""
    if isinstance(value, (int, float)):
        return float(value)
    value = str(value or "").strip().strip("[]")
    m = _TS_RE.match(value)
    if m:
        h, mnt, s = m.groups()
        return int(h or 0) * 3600 + int(mnt) * 60 + float(s)
    try:
        return float(value)
    except ValueError:
        return 0.0


def format_timestamp(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def normalise_segments(raw: list[dict], offset: float, clip_duration: float) -> list[dict]:
    """Shift clip-relative times to recording time, clamp them, keep them in order."""
    out, last = [], 0.0
    for seg in raw:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        t = parse_timestamp(seg.get("start"))
        t = min(max(t, last), clip_duration)  # never go backwards or past the clip end
        last = t
        out.append({
            "start": round(offset + t, 2),
            "speaker": (seg.get("speaker") or "Speaker").strip()[:60],
            "bn": text,
            "en": "",
            "uncertain": bool(seg.get("uncertain")) or "অস্পষ্ট" in text,
        })
    return out


def _tail_text(segments, n=4):
    return "\n".join(f'{s["speaker"]}: {s["bn"]}' for s in segments[-n:])


def transcribe_chunk(job, chunk, chunk_no, chunk_total, previous, depth=0, on_live=None):
    """Transcribe one chunk; if the model's answer gets cut off, split the chunk in two and retry.

    on_live(segments) receives this chunk's segments (recording-time stamps) while they stream in.
    """
    prompt = gemini.transcription_prompt(
        interview_type=job.interview_type,
        context_notes=job.context_notes,
        expected_speakers=job.expected_speakers,
        chunk_no=chunk_no,
        chunk_total=chunk_total,
        previous_tail=_tail_text(previous),
        known_speakers={s["speaker"] for s in previous},
    )
    def streamed(raw_so_far):
        if on_live:
            on_live(normalise_segments(raw_so_far, chunk.offset, chunk.duration))

    try:
        raw = gemini.transcribe_audio_clip(chunk.path.read_bytes(), prompt,
                                           on_segments=streamed if on_live else None)
    except gemini.TruncatedOutput:
        if depth >= 2 or chunk.duration < 90:
            raise
        log.info("Chunk %s truncated; splitting in half", chunk_no)
        halves = audio.make_chunks(chunk.path, chunk.path.parent / f"split_{chunk_no}_{depth}",
                                   chunk_seconds=chunk.duration / 2 + 1, duration=chunk.duration)
        result = []
        for half in halves:
            half.offset += chunk.offset
            done_before = list(result)
            live = (lambda segs, before=done_before: on_live(before + segs)) if on_live else None
            result += transcribe_chunk(job, half, chunk_no, chunk_total, previous + result, depth + 1, live)
        return result
    return normalise_segments(raw, chunk.offset, chunk.duration)


def translate_segments(segments, on_progress=None):
    size = max(1, settings.TRANSLATE_BATCH_SIZE)
    for start in range(0, len(segments), size):
        batch = [{"i": i, "speaker": segments[i]["speaker"], "bn": segments[i]["bn"]}
                 for i in range(start, min(start + size, len(segments)))]
        english = gemini.translate_batch(batch)
        missing = [b["i"] for b in batch if not english.get(b["i"])]
        if missing:  # retry the missing ones once, one small batch
            english.update(gemini.translate_batch([b for b in batch if b["i"] in missing]))
        for b in batch:
            segments[b["i"]]["en"] = english.get(b["i"], "") or "[translation missing — please check]"
        if on_progress:
            on_progress(min(start + size, len(segments)), len(segments))
    return segments


def _clock(seconds):
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def run(job, on_progress=lambda pct, note: None):
    """Full pipeline. Returns the list of segments.

    * Each finished part is saved on the job (job.partial), so a retry continues
      where it stopped instead of starting over.
    * While a part is being transcribed, the segments that have already streamed in
      are saved in job.live_segments, so the user can watch the text appear and can
      download whatever is done if the free tier stops half-way.
    """
    workdir = Path(tempfile.mkdtemp(prefix=f"job{job.pk}_"))
    saved = job.partial or {}
    # a job that already has finished parts keeps the part length it started with,
    # so changing CHUNK_SECONDS later never throws away work
    cut = saved.get("chunk_seconds") if saved.get("chunks") else settings.CHUNK_SECONDS
    done = saved.get("chunks", {})
    ends = saved.get("ends", {})
    total = job.duration_seconds or 1
    span = 73 if job.translate else 93  # share of the progress bar used by transcription

    def save_partial():
        job.partial = {"chunk_seconds": cut, "chunks": done, "ends": ends}
        job.save(update_fields=["partial", "live_segments"])

    def report(reached, n, count):
        pct = 5 + int(span * min(reached, total) / total)
        on_progress(pct, f"Transcribing part {n} of {count} — reached {_clock(reached)} of {_clock(total)}")

    try:
        on_progress(3, "Preparing audio")
        chunks = audio.make_chunks(job.audio_path, workdir, cut, job.duration_seconds)
        segments = []
        for n, chunk in enumerate(chunks, start=1):
            key = f"{chunk.offset:.2f}"
            if key in done:
                segments += done[key]
                continue
            report(chunk.offset, n, len(chunks))

            def on_live(segs, n=n, chunk=chunk):
                old = job.live_segments or []
                # keep whichever preview reaches further (a retry starts again from the part's start)
                if old and segs and old[-1]["start"] > segs[-1]["start"] and old[0]["start"] >= chunk.offset:
                    return
                job.live_segments = segs
                job.save(update_fields=["live_segments"])
                if segs:
                    report(segs[-1]["start"], n, len(chunks))

            part = transcribe_chunk(job, chunk, n, len(chunks), segments, on_live=on_live)
            segments += part
            done[key] = part
            ends[key] = round(chunk.offset + chunk.duration, 2)
            job.live_segments = []
            save_partial()
        if not segments:
            raise gemini.GeminiError("No speech was found in this recording.")
        if job.translate:
            on_progress(78, "Translating to English")
            translate_segments(
                segments,
                on_progress=lambda d, t: on_progress(78 + int(20 * d / t), f"Translating ({d}/{t})"),
            )
        return segments
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
