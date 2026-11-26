from django.db import models


class Notification(models.Model):
    """Messages shown under the bell in the desktop app."""

    class Kind(models.TextChoices):
        DONE = "done", "Transcript ready"
        FAILED = "failed", "Stopped"
        UPDATE = "update", "New version"
        INFO = "info", "Information"

    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.INFO)
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    url = models.CharField(max_length=500, blank=True)
    # the same event never shows twice (e.g. "done:12:2026-11-26T10:00")
    dedupe_key = models.CharField(max_length=200, unique=True, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at", "-id")

    def __str__(self):
        return self.title
