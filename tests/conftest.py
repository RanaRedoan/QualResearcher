import subprocess

import pytest


@pytest.fixture(autouse=True)
def _settings(settings, tmp_path):
    settings.PRIVATE_MEDIA_ROOT = tmp_path / "private"
    settings.GEMINI_API_KEY = "test-key"
    settings.GEMINI_MAX_RETRIES = 0
    settings.STORAGES = {**settings.STORAGES, "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
    return settings


@pytest.fixture(scope="session")
def tone_file(tmp_path_factory):
    """200 s of audio: 8 s tone, 2 s silence, repeated."""
    path = tmp_path_factory.mktemp("audio") / "interview.mp3"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=200",
         "-af", "volume='if(lt(mod(t,10),8),1,0)':eval=frame", "-ac", "1", str(path)],
        check=True,
    )
    return path


@pytest.fixture
def fake_gemini(monkeypatch):
    """Replace the Gemini calls with predictable fakes and record what was sent."""
    from transcription import gemini

    calls = {"transcribe": [], "translate": []}

    def transcribe(audio_bytes, prompt, mime_type="audio/mp3", on_segments=None):
        calls["transcribe"].append(prompt)
        n = len(calls["transcribe"])
        segs = [
            {"start": "00:00", "speaker": "Interviewer", "text": f"প্রশ্ন {n}", "uncertain": False},
            {"start": "00:05", "speaker": "Respondent", "text": f"উত্তর {n} [অস্পষ্ট]", "uncertain": True},
        ]
        if on_segments:  # behave like a streamed answer
            on_segments(segs[:1])
            on_segments(segs)
        return segs

    def translate(segments):
        calls["translate"].append(segments)
        return {s["i"]: f"EN: {s['bn']}" for s in segments}

    monkeypatch.setattr(gemini, "transcribe_audio_clip", transcribe)
    monkeypatch.setattr(gemini, "translate_batch", translate)
    return calls


@pytest.fixture
def sync_runner(monkeypatch):
    from transcription import runner
    from transcription.tasks import process_job, translate_job

    def run_now(job_id, kind="transcribe", delay=0):
        (translate_job if kind == "translate" else process_job)(job_id)

    monkeypatch.setattr(runner, "enqueue", run_now)
