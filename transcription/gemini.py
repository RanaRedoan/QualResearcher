"""All calls to the Gemini API live here, so the model can be swapped in one place."""
import json
import logging
import random
import re
import threading
import time
from contextlib import contextmanager

from django.conf import settings

log = logging.getLogger(__name__)


class GeminiError(Exception):
    pass


class ModelUnavailable(GeminiError):
    """Busy, rate-limited, missing or unreachable model — worth trying again or another model."""


class BusyError(GeminiError):
    """Every model stayed busy / rate-limited through all retries. Worth retrying later."""


class DailyLimitError(GeminiError):
    """The free-tier daily quota is used up. Retrying today will not help."""


class TruncatedOutput(GeminiError):
    """The model hit its output limit; the caller should retry with shorter audio."""


TRANSCRIPT_SCHEMA = {
    "type": "object",
    "properties": {
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "start": {"type": "string", "description": "MM:SS from the start of THIS clip"},
                    "speaker": {"type": "string"},
                    "text": {"type": "string", "description": "Verbatim Bangla (Bengali script)"},
                },
                "required": ["start", "speaker", "text"],
            },
        }
    },
    "required": ["segments"],
}

TRANSLATION_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"i": {"type": "integer"}, "en": {"type": "string"}},
                "required": ["i", "en"],
            },
        }
    },
    "required": ["items"],
}

SPEAKER_SCHEMES = {
    "FGD": 'Use "Moderator" for the facilitator, "Note-taker" if they speak, and "Participant 1", '
           '"Participant 2", … for group members (number them in the order they first speak).',
    "default": 'Use "Interviewer" for the person asking questions and "Respondent" for the person '
               'answering. Anyone else who speaks: "Other 1", "Other 2", ….',
}


def english_rule():
    if _english_words_script() == "latin":
        return "2. English words spoken inside Bangla speech stay in English (Latin script), e.g. 'NGO', 'loan'."
    return ("2. Write EVERYTHING in Bengali script. English words spoken inside Bangla speech are written "
            "in Bengali letters as they sound (loan → লোন, process → প্রসেস, NGO → এনজিও, CIB → সিআইবি), "
            "and numbers use Bengali digits (৫০ লাখ). Do not use any Latin letters.")


def transcription_prompt(*, interview_type, context_notes, expected_speakers, chunk_no, chunk_total,
                         previous_tail, known_speakers):
    speakers = SPEAKER_SCHEMES.get(interview_type, SPEAKER_SCHEMES["default"])
    parts = [
        "You are an expert Bangla transcriptionist working for social-science researchers.",
        "Transcribe this audio clip VERBATIM in Bangla (Bengali script).",
        "",
        "Rules:",
        "1. Write every spoken word exactly as said — keep dialect, repetitions, false starts and "
        "fillers (যেমন: আ, উম, মানে). Never summarise, shorten, correct grammar or skip anything.",
        english_rule(),
        "3. Start a new segment at every change of speaker, and also every 30–60 seconds inside a long turn.",
        "4. 'start' is the time the segment begins, as MM:SS measured from the beginning of THIS clip.",
        f"5. Speaker labels: {speakers}",
        "6. Mark non-verbal sounds inline in square brackets in Bangla: [হাসি], [কান্না], [কাশি], "
        "[দীর্ঘ নীরবতা], [একসাথে কথা], [পেছনে শব্দ].",
        "7. If a word or phrase cannot be heard clearly, write your best guess followed by [অস্পষ্ট], "
        "or just [অস্পষ্ট] if nothing can be guessed.",
        "8. The clip may begin or end in the middle of a sentence — transcribe exactly what is audible.",
    ]
    if chunk_total > 1:
        parts.append(f"\nThis is part {chunk_no} of {chunk_total} of one longer recording.")
    if expected_speakers:
        parts.append(f"The full recording has about {expected_speakers} speakers.")
    if known_speakers:
        parts.append("Speaker labels already used earlier in this recording (keep them consistent): "
                     + ", ".join(sorted(known_speakers)))
    if previous_tail:
        parts.append("For continuity, the end of the previous part was:\n" + previous_tail)
    if context_notes:
        parts.append("Background from the researcher (use it to spell names and terms correctly; "
                     "do NOT add it to the transcript):\n" + context_notes.strip()[:2000])
    return "\n".join(parts)


TRANSLATION_PROMPT = """You translate Bangla qualitative-research interview transcripts into English.

You get a JSON list of segments, each with an index i, a speaker and Bangla text.
Return the English translation of EVERY segment with the same i.

Rules:
- Translate faithfully and completely. Do not summarise, merge, drop or reorder segments.
- Write natural, clear English that keeps the speaker's meaning, tone and hesitations.
- Translate bracketed cues to English: [হাসি]→[laughs], [কান্না]→[crying], [কাশি]→[coughs],
  [দীর্ঘ নীরবতা]→[long pause], [একসাথে কথা]→[crosstalk], [পেছনে শব্দ]→[background noise],
  [অস্পষ্ট]→[inaudible].
- Keep English words that were already in English.
- For culturally specific terms with no exact English word (e.g. kinship terms, local
  institutions, measures like 'bigha'), keep the term in italics-free transliteration and add a short
  gloss in parentheses, e.g. "khala (maternal aunt)".
- Keep names of people and places as they are.

Segments:
"""


def _english_words_script():
    """The desktop app's Settings choice wins over ENGLISH_WORDS_SCRIPT from the environment."""
    if settings.DESKTOP:
        from desktop.store import load

        choice = load()["english_words"]
        if choice in ("bangla", "latin"):
            return choice
    return settings.ENGLISH_WORDS_SCRIPT


_job_key = threading.local()


@contextmanager
def use_key(key_id):
    """Run the Gemini calls inside this block with one of the app's saved keys (desktop app)."""
    previous = getattr(_job_key, "id", "")
    _job_key.id = key_id or ""
    try:
        yield
    finally:
        _job_key.id = previous


def _client():
    from desktop.store import api_key

    key = api_key(getattr(_job_key, "id", ""))
    if not key:
        raise GeminiError("No Gemini API key is set. Add it in Settings." if settings.DESKTOP
                          else "GEMINI_API_KEY is not set on the server.")
    from google import genai

    return genai.Client(api_key=key)


TRANSIENT_CODES = {408, 429, 500, 502, 503, 504}
_RETRY_DELAY_RE = re.compile(r"retry(?:Delay| in)['\":\s]*([\d.]+)\s*s", re.I)


def _models(primary):
    """Primary model first, then the fallback (if set and different)."""
    fallback = (settings.GEMINI_FALLBACK_MODEL or "").strip()
    return [primary] + ([fallback] if fallback and fallback != primary else [])


def _wait_seconds(attempt, exc=None):
    """Use the wait Google asks for (429 'retryDelay'), else 5, 10, 20, 40, 80, 90 … seconds."""
    if exc is not None:
        m = _RETRY_DELAY_RE.search(str(exc))
        if m:
            return min(120, float(m.group(1)) + 2)
    return min(90, 5 * 2 ** (attempt - 1)) + random.random() * 3


def _is_daily_quota(exc):
    text = str(exc).lower()
    return "perday" in text or "per day" in text or "daily" in text


def _config(schema, temperature, thinking=True):
    from google.genai import types

    kwargs = dict(response_mime_type="application/json", response_json_schema=schema,
                  temperature=temperature, max_output_tokens=65536)
    if thinking and settings.GEMINI_THINKING_LEVEL:
        # Transcription needs listening, not reasoning: low thinking = faster, fewer tokens, fewer timeouts.
        kwargs["thinking_config"] = types.ThinkingConfig(thinking_level=settings.GEMINI_THINKING_LEVEL)
    return types.GenerateContentConfig(**kwargs)


def _finish_reason(response):
    try:
        return str(response.candidates[0].finish_reason or "") if response.candidates else ""
    except (AttributeError, IndexError):
        return ""


def _chunk_text(response):
    try:
        return response.text or ""
    except Exception:  # a streamed piece without text parts
        return ""


def _request(client, model, contents, config, on_text=None):
    """One API call. With on_text, the answer is streamed and on_text(text_so_far) is called
    as it grows, so the user can watch the transcript appear. Returns (text, finish_reason)."""
    if on_text is None:
        response = client.models.generate_content(model=model, contents=contents, config=config)
        return _chunk_text(response), _finish_reason(response)
    text, finish = "", ""
    for piece in client.models.generate_content_stream(model=model, contents=contents, config=config):
        add = _chunk_text(piece)
        if add:
            text += add
            try:
                on_text(text)
            except Exception:  # a display problem must never stop the transcription
                log.exception("live preview callback failed")
        finish = _finish_reason(piece) or finish
    return text, finish


def _call_one_model(client, model, contents, schema, temperature, on_text=None):
    """Try one model, retrying busy/rate-limit/network errors. Returns parsed JSON."""
    from google.genai import errors

    config = _config(schema, temperature)
    last_error = None
    attempt = 0
    while attempt <= settings.GEMINI_MAX_RETRIES:
        attempt += 1
        exc_for_wait = None
        try:
            text, finish = _request(client, model, contents, config, on_text)
            if "MAX_TOKENS" in finish:
                raise TruncatedOutput("Model output was cut off (MAX_TOKENS).")
            if "SAFETY" in finish or "PROHIBITED" in finish:
                raise GeminiError(f"Gemini refused this content ({finish}).")
            return json.loads(text)
        except (TruncatedOutput, GeminiError):
            raise
        except json.JSONDecodeError as exc:
            last_error = GeminiError(f"Gemini returned invalid JSON: {exc}")
        except errors.APIError as exc:
            code = getattr(exc, "code", 0) or 0
            if code == 400 and "thinking" in str(exc).lower() and config.thinking_config is not None:
                config = _config(schema, temperature, thinking=False)  # this model has no thinking control
                attempt -= 1  # not a real failure; try again straight away
                continue
            if code == 404:
                raise ModelUnavailable(f"Model '{model}' was not found or is not available to this key.") from exc
            if code == 429 and _is_daily_quota(exc):
                raise DailyLimitError(
                    "The free Gemini quota for today is used up. Finished parts are saved — press "
                    "'Try again' tomorrow, or turn on billing for unlimited use."
                ) from exc
            if code not in TRANSIENT_CODES:
                raise GeminiError(f"Gemini API error {code}: {exc}") from exc
            last_error = ModelUnavailable(f"Gemini API error {code} ({model}): {exc}")
            exc_for_wait = exc
        except Exception as exc:  # network hiccups etc.
            last_error = ModelUnavailable(f"Gemini call failed ({model}): {exc}")
        if attempt <= settings.GEMINI_MAX_RETRIES:
            wait = _wait_seconds(attempt, exc_for_wait)
            log.warning("Gemini %s attempt %s failed (%s); retrying in %.0fs", model, attempt, last_error, wait)
            time.sleep(wait)
    raise last_error


def _generate(model, contents, schema, temperature=0.2, on_text=None):
    """Call Gemini with JSON output. Busy/rate-limited models are retried with growing waits,
    then the fallback model is tried. Bad key / bad request errors stop immediately."""
    client = _client()
    last_error = None
    for name in _models(model):
        try:
            return _call_one_model(client, name, contents, schema, temperature, on_text)
        except ModelUnavailable as exc:
            last_error = exc
            log.warning("Model %s unavailable, trying fallback if any: %s", name, exc)
    raise BusyError(
        f"Google's AI servers are busy right now ({last_error}). Finished parts are saved."
    )


_DECODER = json.JSONDecoder()


def segments_so_far(text: str) -> list[dict]:
    """Pull the complete segment objects out of a JSON answer that is still arriving."""
    found, pos = [], max(text.find("["), 0)
    while True:
        idx = text.find("{", pos)
        if idx < 0:
            return found
        try:
            obj, end = _DECODER.raw_decode(text, idx)
        except json.JSONDecodeError:
            pos = idx + 1
            continue
        if isinstance(obj, dict) and obj.get("text"):
            found.append(obj)
        pos = end


def transcribe_audio_clip(audio_bytes: bytes, prompt: str, mime_type="audio/mp3", on_segments=None) -> list[dict]:
    """on_segments(list) is called whenever more finished segments have streamed in."""
    from google.genai import types

    on_text = None
    if on_segments is not None:
        seen = {"n": 0}

        def on_text(text):
            segs = segments_so_far(text)
            if len(segs) > seen["n"]:
                seen["n"] = len(segs)
                on_segments(segs)

    data = _generate(
        settings.GEMINI_TRANSCRIBE_MODEL,
        [types.Part.from_bytes(data=audio_bytes, mime_type=mime_type), prompt],
        TRANSCRIPT_SCHEMA,
        on_text=on_text,
    )
    return data.get("segments", [])


def translate_batch(segments: list[dict]) -> dict[int, str]:
    """segments: [{"i": 0, "speaker": "...", "bn": "..."}] → {i: english}"""
    payload = json.dumps(
        [{"i": s["i"], "speaker": s["speaker"], "text": s["bn"]} for s in segments], ensure_ascii=False
    )
    data = _generate(settings.GEMINI_TRANSLATE_MODEL, [TRANSLATION_PROMPT + payload], TRANSLATION_SCHEMA)
    return {int(item["i"]): item.get("en", "") for item in data.get("items", []) if "i" in item}
