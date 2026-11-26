"""Create the desktop app's notifications. Everything here does nothing outside the desktop app."""
import logging

from django.conf import settings
from django.db import IntegrityError, transaction

log = logging.getLogger(__name__)

CHECK_REMINDER = ("Please check it against the audio before you use it. AI can mis-hear words, "
                  "names and who is speaking.")


def notify(kind, title, body="", url="", key=None):
    if not settings.DESKTOP:
        return None
    from .models import Notification

    try:
        with transaction.atomic():
            return Notification.objects.create(kind=kind, title=title[:200], body=body, url=url, dedupe_key=key)
    except IntegrityError:  # already told the user about this
        return None
    except Exception:
        log.exception("Could not save a notification")
        return None


def job_event(job, event):
    """event: "done", "translated" or "failed"."""
    if not settings.DESKTOP:
        return
    stamp = (job.finished_at or job.created_at).isoformat(timespec="seconds")
    if event == "done":
        notify("done", f"“{job.title}” is ready", CHECK_REMINDER, job.get_absolute_url(), f"done:{job.pk}:{stamp}")
    elif event == "translated":
        notify("done", f"English translation of “{job.title}” is ready",
               "Check the translation too, especially local terms and names.",
               job.get_absolute_url(), f"translated:{job.pk}:{stamp}")
    elif event == "failed":
        notify("failed", f"“{job.title}” stopped",
               f"Reached {job.covered_display} of {job.duration_display}. Open it to continue from where it stopped "
               "(you can pick another Gemini key there).",
               job.get_absolute_url(), f"failed:{job.pk}:{stamp}")


def on_app_start():
    """Run once when the desktop app opens."""
    if not settings.DESKTOP:
        return
    from transcription.models import Job

    notify("info", f"Welcome to {settings.SITE_NAME} (beta)",
           "This is a beta version. Transcripts are made by AI and must be checked by a person before use. "
           f"If something goes wrong or looks odd, email {settings.DEVELOPER_EMAIL}.",
           key="welcome-beta")
    # jobs that were running when the app was closed
    unfinished = Job.objects.filter(status__in=["queued", "processing", "translating"]).defer("segments", "live_segments")
    for job in unfinished:
        notify("failed", f"“{job.title}” was stopped when the app closed",
               f"Reached {job.covered_display} of {job.duration_display}. Open it and press Continue to finish it.",
               job.get_absolute_url(), f"stopped:{job.pk}:{job.covered_display}:{job.progress}")


def update_available(version, url):
    notify("update", f"Version {version} is available",
           "Download the new installer and install it over this one. Your transcripts, keys and settings are kept.",
           url, f"update:{version}")
