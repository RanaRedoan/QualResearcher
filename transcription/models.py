import math
import uuid
from pathlib import Path

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone


class Job(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        PROCESSING = "processing", "Transcribing"
        TRANSLATING = "translating", "Translating"
        DONE = "done", "Ready"
        FAILED = "failed", "Failed"

    class InterviewType(models.TextChoices):
        IDI = "IDI", "In-depth interview (IDI)"
        KII = "KII", "Key informant interview (KII)"
        FGD = "FGD", "Focus group discussion (FGD)"
        CASE = "CASE", "Case study"
        OTHER = "OTHER", "Other"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="jobs")
    title = models.CharField(max_length=200)
    interview_type = models.CharField(max_length=10, choices=InterviewType.choices, default=InterviewType.IDI)
    expected_speakers = models.PositiveSmallIntegerField(null=True, blank=True)
    context_notes = models.TextField(
        blank=True,
        help_text="Topic, place names, technical terms — helps the AI spell them correctly.",
    )
    demographics = models.JSONField(default=dict, blank=True)

    original_filename = models.CharField(max_length=255)
    audio_relpath = models.CharField(max_length=255, blank=True)
    file_size = models.BigIntegerField(default=0)
    duration_seconds = models.FloatField(default=0)
    billed_minutes = models.PositiveIntegerField(default=0)

    translate = models.BooleanField("English translation", default=False)
    auto_retries = models.PositiveSmallIntegerField(default=0)
    consent_confirmed = models.BooleanField(default=False)
    retention_days = models.PositiveSmallIntegerField(default=1)
    audio_delete_after = models.DateTimeField(null=True, blank=True)
    audio_deleted_at = models.DateTimeField(null=True, blank=True)

    status = models.CharField(max_length=12, choices=Status.choices, default=Status.QUEUED)
    progress = models.PositiveSmallIntegerField(default=0)
    progress_note = models.CharField(max_length=200, blank=True)
    error = models.TextField(blank=True)

    # [{"start": 12.0, "speaker": "Interviewer", "bn": "...", "en": "...", "uncertain": false}, ...]
    segments = models.JSONField(default=list, blank=True)
    model_used = models.CharField(max_length=80, blank=True)
    # finished parts of a long recording, so "Try again" continues instead of starting over
    partial = models.JSONField(default=dict, blank=True)
    source = models.CharField(max_length=10, default="web")  # "web" upload or "folder" command
    # segments of the part being transcribed right now (streamed in, shown live)
    live_segments = models.JSONField(default=list, blank=True)
    # desktop app: which saved Gemini key (by its id) this job uses; empty = the default key
    gemini_key_id = models.CharField(max_length=32, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"#{self.pk} {self.title}"

    def get_absolute_url(self):
        return reverse("job_detail", args=[self.public_id])

    # --- audio file helpers -------------------------------------------------
    @property
    def audio_path(self) -> Path | None:
        if not self.audio_relpath:
            return None
        return Path(settings.PRIVATE_MEDIA_ROOT) / self.audio_relpath

    @property
    def has_audio(self):
        return bool(self.audio_relpath) and self.audio_deleted_at is None

    def delete_audio(self):
        path = self.audio_path
        if path and path.exists():
            path.unlink()
        if self.audio_relpath and self.audio_deleted_at is None:
            self.audio_deleted_at = timezone.now()
            self.save(update_fields=["audio_deleted_at"])

    def delete(self, *args, **kwargs):
        path = self.audio_path
        if path and path.exists():
            path.unlink()
        return super().delete(*args, **kwargs)

    # --- display helpers ----------------------------------------------------
    @staticmethod
    def minutes_for(duration_seconds):
        return max(settings.MIN_BILLABLE_MINUTES, math.ceil(duration_seconds / 60))

    @property
    def duration_display(self):
        s = int(round(self.duration_seconds))
        return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"

    @property
    def is_finished(self):
        return self.status in {self.Status.DONE, self.Status.FAILED}

    def available_segments(self):
        """Everything transcribed so far: the full transcript when done, otherwise the
        finished parts plus whatever has streamed in for the current part."""
        chunks = (self.partial or {}).get("chunks", {})
        if self.segments and (self.status == self.Status.DONE or not chunks):
            return self.segments  # finished (or being translated afterwards)
        segs = [s for key in sorted(chunks, key=float) for s in chunks[key]]
        last = segs[-1]["start"] if segs else -1
        segs += [s for s in (self.live_segments or []) if s["start"] >= last]
        return segs

    @property
    def covered_seconds(self):
        """How far into the recording the transcript reaches (approximately)."""
        if self.status == self.Status.DONE or (self.segments and not (self.partial or {}).get("chunks")):
            return self.duration_seconds
        ends = [float(v) for v in (self.partial or {}).get("ends", {}).values()]
        live = [s["start"] for s in (self.live_segments or [])]
        return min(max(ends + live + [0.0]), self.duration_seconds)

    @property
    def covered_display(self):
        s = int(self.covered_seconds)
        return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"

    @property
    def has_partial(self):
        return self.status != self.Status.DONE and bool(self.available_segments())

    @property
    def has_english(self):
        return bool(self.segments) and all(s.get("en") for s in self.segments)

    @property
    def uncertain_count(self):
        return sum(1 for s in self.segments if s.get("uncertain"))
