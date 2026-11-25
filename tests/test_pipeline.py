import pytest

from transcription import audio, gemini, pipeline


def test_parse_timestamp_formats():
    assert pipeline.parse_timestamp("01:30") == 90
    assert pipeline.parse_timestamp("1:02:03") == 3723
    assert pipeline.parse_timestamp("[00:07]") == 7
    assert pipeline.parse_timestamp(12.5) == 12.5
    assert pipeline.parse_timestamp("garbage") == 0.0


def test_normalise_segments_offsets_clamps_and_orders():
    raw = [
        {"start": "00:10", "speaker": "Interviewer", "text": "ক", "uncertain": False},
        {"start": "00:05", "speaker": "Respondent", "text": "খ", "uncertain": False},  # goes backwards
        {"start": "99:00", "speaker": "Respondent", "text": "গ [অস্পষ্ট]", "uncertain": False},  # past clip end
        {"start": "00:20", "speaker": "Respondent", "text": "   ", "uncertain": False},  # empty → dropped
    ]
    out = pipeline.normalise_segments(raw, offset=600, clip_duration=300)
    assert [s["start"] for s in out] == [610, 610, 900]
    assert out[2]["uncertain"] is True  # flagged because of [অস্পষ্ট]
    assert len(out) == 3


def test_cut_points_snap_to_pauses():
    silences = [(118.0, 121.0), (240.0, 240.5), (365.0, 368.0)]
    cuts = audio.plan_cut_points(duration=500, chunk_seconds=120, silences=silences)
    assert cuts[0] == 119.5
    assert all(b - a > 60 for a, b in zip(cuts, cuts[1:]))
    assert cuts[-1] < 500


def test_cut_points_never_loop_forever_without_silences():
    cuts = audio.plan_cut_points(duration=3 * 3600, chunk_seconds=600, silences=[])
    assert len(cuts) == 17


def test_make_chunks_cover_whole_recording(tone_file, tmp_path):
    duration = audio.probe_duration(tone_file)
    chunks = audio.make_chunks(tone_file, tmp_path / "chunks", chunk_seconds=60, duration=duration)
    assert len(chunks) >= 3
    assert chunks[0].offset == 0
    assert abs(sum(c.duration for c in chunks) - duration) < 0.5
    # cuts land in the silent gaps (8–10 s of every 10 s)
    for c in chunks[1:]:
        assert 7.5 <= c.offset % 10 <= 10.0
    assert all(c.path.stat().st_size > 0 for c in chunks)


def test_probe_rejects_non_audio(tmp_path):
    bad = tmp_path / "notes.mp3"
    bad.write_text("not audio")
    with pytest.raises(audio.AudioError):
        audio.probe_duration(bad)


def test_prompt_mentions_continuity_and_context():
    prompt = gemini.transcription_prompt(
        interview_type="FGD", context_notes="Study on microfinance in Kurigram", expected_speakers=6,
        chunk_no=2, chunk_total=4, previous_tail="Moderator: আচ্ছা", known_speakers={"Moderator", "Participant 1"},
    )
    assert "part 2 of 4" in prompt and "Participant 1" in prompt and "Kurigram" in prompt


def test_truncated_chunk_is_split_and_retried(tone_file, tmp_path, monkeypatch, settings):
    settings.TRANSLATE_BATCH_SIZE = 50
    calls = []

    def flaky(audio_bytes, prompt, mime_type="audio/mp3", on_segments=None):
        calls.append(len(audio_bytes))
        if len(calls) == 1:
            raise gemini.TruncatedOutput("cut off")
        return [{"start": "00:01", "speaker": "Respondent", "text": f"অংশ {len(calls)}", "uncertain": False}]

    monkeypatch.setattr(gemini, "transcribe_audio_clip", flaky)

    class FakeJob:
        interview_type, context_notes, expected_speakers = "IDI", "", None

    chunk = audio.make_chunks(tone_file, tmp_path / "c", chunk_seconds=600)[0]
    segs = pipeline.transcribe_chunk(FakeJob(), chunk, 1, 1, [])
    assert len(calls) >= 3  # 1 failed full chunk + at least 2 halves
    assert len(segs) == len(calls) - 1
    assert segs[1]["start"] > segs[0]["start"]  # second half is offset in time


def test_translate_fills_missing(monkeypatch):
    def partial(batch):
        return {b["i"]: "ok" for b in batch if b["i"] % 2 == 0}

    monkeypatch.setattr(gemini, "translate_batch", partial)
    segs = [{"speaker": "A", "bn": str(i), "en": ""} for i in range(4)]
    pipeline.translate_segments(segs)
    assert segs[0]["en"] == "ok"
    assert "missing" in segs[1]["en"]


def test_generate_builds_valid_request_and_retries(monkeypatch, settings):
    """Exercise the real google-genai types with a fake client."""
    import json as _json
    from types import SimpleNamespace

    from google.genai import errors, types

    settings.GEMINI_MAX_RETRIES = 2
    monkeypatch.setattr(gemini.time, "sleep", lambda s: None)
    seen = []

    class FakeModels:
        def generate_content(self, model, contents, config):
            assert isinstance(config, types.GenerateContentConfig)
            assert config.response_json_schema == gemini.TRANSCRIPT_SCHEMA
            assert isinstance(contents[0], types.Part)
            seen.append(model)
            if len(seen) == 1:
                raise errors.ServerError(503, {"error": {"message": "overloaded"}})
            cand = SimpleNamespace(finish_reason=types.FinishReason.STOP)
            return SimpleNamespace(candidates=[cand], text=_json.dumps({"segments": [
                {"start": "00:01", "speaker": "Respondent", "text": "হ্যাঁ", "uncertain": False}]}))

    monkeypatch.setattr(gemini, "_client", lambda: SimpleNamespace(models=FakeModels()))
    segs = gemini.transcribe_audio_clip(b"ID3", "prompt")
    assert segs[0]["text"] == "হ্যাঁ" and len(seen) == 2


def test_generate_raises_truncated_on_max_tokens(monkeypatch):
    from types import SimpleNamespace

    from google.genai import types

    class FakeModels:
        def generate_content(self, **kw):
            return SimpleNamespace(candidates=[SimpleNamespace(finish_reason=types.FinishReason.MAX_TOKENS)], text="{")

    monkeypatch.setattr(gemini, "_client", lambda: SimpleNamespace(models=FakeModels()))
    with pytest.raises(gemini.TruncatedOutput):
        gemini.transcribe_audio_clip(b"x", "p")


def test_bad_api_key_fails_fast(monkeypatch):
    from types import SimpleNamespace

    from google.genai import errors

    calls = []

    class FakeModels:
        def generate_content(self, **kw):
            calls.append(1)
            raise errors.ClientError(400, {"error": {"message": "API key not valid"}})

    monkeypatch.setattr(gemini, "_client", lambda: SimpleNamespace(models=FakeModels()))
    with pytest.raises(gemini.GeminiError, match="400"):
        gemini.transcribe_audio_clip(b"x", "p")
    assert len(calls) == 1


def test_busy_model_falls_back_to_second_model(monkeypatch, settings):
    from types import SimpleNamespace

    from google.genai import errors, types

    settings.GEMINI_MAX_RETRIES = 1
    settings.GEMINI_TRANSCRIBE_MODEL, settings.GEMINI_FALLBACK_MODEL = "main", "backup"
    monkeypatch.setattr(gemini.time, "sleep", lambda s: None)
    seen = []

    class FakeModels:
        def generate_content(self, model, contents, config):
            seen.append(model)
            if model == "main":
                raise errors.ServerError(503, {"error": {"message": "high demand"}})
            return SimpleNamespace(candidates=[SimpleNamespace(finish_reason=types.FinishReason.STOP)],
                                   text='{"segments": []}')

    monkeypatch.setattr(gemini, "_client", lambda: SimpleNamespace(models=FakeModels()))
    assert gemini.transcribe_audio_clip(b"x", "p") == []
    assert seen == ["main", "main", "backup"]


def test_all_models_busy_gives_friendly_error(monkeypatch, settings):
    from types import SimpleNamespace

    from google.genai import errors

    settings.GEMINI_MAX_RETRIES = 0
    monkeypatch.setattr(gemini.time, "sleep", lambda s: None)

    class FakeModels:
        def generate_content(self, **kw):
            raise errors.ServerError(503, {"error": {"message": "high demand"}})

    monkeypatch.setattr(gemini, "_client", lambda: SimpleNamespace(models=FakeModels()))
    with pytest.raises(gemini.BusyError, match="busy"):
        gemini.transcribe_audio_clip(b"x", "p")


def test_daily_free_quota_stops_with_clear_message(monkeypatch):
    from types import SimpleNamespace

    from google.genai import errors

    class FakeModels:
        def generate_content(self, **kw):
            raise errors.ClientError(429, {"error": {"message": "Quota exceeded for GenerateRequestsPerDayPerProjectPerModel-FreeTier"}})

    monkeypatch.setattr(gemini, "_client", lambda: SimpleNamespace(models=FakeModels()))
    with pytest.raises(gemini.DailyLimitError, match="tomorrow"):
        gemini.transcribe_audio_clip(b"x", "p")


def test_rate_limit_waits_as_long_as_google_asks(monkeypatch, settings):
    from types import SimpleNamespace

    from google.genai import errors, types

    settings.GEMINI_MAX_RETRIES = 2
    waits = []
    monkeypatch.setattr(gemini.time, "sleep", waits.append)
    calls = []

    class FakeModels:
        def generate_content(self, **kw):
            calls.append(1)
            if len(calls) == 1:
                raise errors.ClientError(429, {"error": {"message": "Resource exhausted. Please retry in 37.5s."}})
            return SimpleNamespace(candidates=[SimpleNamespace(finish_reason=types.FinishReason.STOP)], text='{"segments": []}')

    monkeypatch.setattr(gemini, "_client", lambda: SimpleNamespace(models=FakeModels()))
    gemini.transcribe_audio_clip(b"x", "p")
    assert waits and 39 <= waits[0] <= 40


def test_thinking_setting_dropped_if_model_rejects_it(monkeypatch, settings):
    from types import SimpleNamespace

    from google.genai import errors, types

    settings.GEMINI_THINKING_LEVEL = "low"
    configs = []

    class FakeModels:
        def generate_content(self, model, contents, config):
            configs.append(config.thinking_config)
            if config.thinking_config is not None:
                raise errors.ClientError(400, {"error": {"message": "thinking_level is not supported for this model"}})
            return SimpleNamespace(candidates=[SimpleNamespace(finish_reason=types.FinishReason.STOP)], text='{"segments": []}')

    monkeypatch.setattr(gemini, "_client", lambda: SimpleNamespace(models=FakeModels()))
    assert gemini.transcribe_audio_clip(b"x", "p") == []
    assert configs[0] is not None and configs[-1] is None


def test_prompt_asks_for_bangla_script_by_default(settings):
    settings.ENGLISH_WORDS_SCRIPT = "bangla"
    prompt = gemini.transcription_prompt(interview_type="IDI", context_notes="", expected_speakers=None,
                                         chunk_no=1, chunk_total=1, previous_tail="", known_speakers=set())
    assert "লোন" in prompt and "Latin" in prompt


def test_bangla_labels():
    from transcription.report import bangla_label

    assert bangla_label("Participant 12") == "অংশগ্রহণকারী ১২"
    assert bangla_label("Interviewer") == "প্রশ্নকারী"
    assert bangla_label("Dr. Rahman") == "Dr. Rahman"


def test_segments_so_far_reads_unfinished_json():
    text = ('{"segments": [{"start": "00:01", "speaker": "Interviewer", "text": "বলুন {ঠিক আছে}", '
            '"uncertain": false}, {"start": "00:05", "speaker": "Respondent", "text": "আমি')
    assert [s["text"] for s in gemini.segments_so_far(text)] == ["বলুন {ঠিক আছে}"]
    assert len(gemini.segments_so_far(text + ' ভালো", "uncertain": false}]}')) == 2


def test_streaming_reports_segments_as_they_arrive(monkeypatch):
    from types import SimpleNamespace

    from google.genai import types

    full = ('{"segments": [{"start": "00:01", "speaker": "Interviewer", "text": "এক", "uncertain": false}, '
            '{"start": "00:04", "speaker": "Respondent", "text": "দুই", "uncertain": false}]}')
    pieces = [full[:60], full[60:110], full[110:]]

    class FakeModels:
        def generate_content_stream(self, model, contents, config):
            for i, p in enumerate(pieces):
                fr = types.FinishReason.STOP if i == len(pieces) - 1 else None
                yield SimpleNamespace(text=p, candidates=[SimpleNamespace(finish_reason=fr)])

    monkeypatch.setattr(gemini, "_client", lambda: SimpleNamespace(models=FakeModels()))
    seen = []
    result = gemini.transcribe_audio_clip(b"x", "p", on_segments=lambda segs: seen.append(len(segs)))
    assert [s["text"] for s in result] == ["এক", "দুই"]
    assert seen == [1, 2]
