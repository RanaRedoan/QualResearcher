import logging

try:
    from celery import shared_task
except ImportError:  # desktop app: jobs run in a thread (runner.py), Celery is not installed
    def shared_task(*args, **kwargs):
        if len(args) == 1 and callable(args[0]) and not kwargs:
            return args[0]
        return lambda func: func
from django.conf import settings
from django.core.mail import send_mail
from django.db.models import Q
from django.utils import timezone

from billing.services import refund_job

from . import gemini, pipeline, runner
from .models import Job

log = logging.getLogger(__name__)


def _update(job, **fields):
    for k, v in fields.items():
        setattr(job, k, v)
    job.save(update_fields=list(fields))


@shared_task(acks_late=True, max_retries=0, time_limit=60 * 60 * 6, soft_time_limit=60 * 60 * 6 - 60)
def process_job(job_id):
    job = Job.objects.select_related("user").filter(pk=job_id).first()
    if not job or job.status not in (Job.Status.QUEUED, Job.Status.PROCESSING, Job.Status.TRANSLATING):
        return
    if not job.has_audio:
        _update(job, status=Job.Status.FAILED, error="The audio file is no longer available.")
        refund_job(job)
        return

    _update(job, status=Job.Status.PROCESSING, started_at=timezone.now(), error="")

    def progress(pct, note):
        status = Job.Status.TRANSLATING if note.startswith("Translat") else Job.Status.PROCESSING
        _update(job, progress=pct, progress_note=note, status=status)

    try:
        with gemini.use_key(job.gemini_key_id):
            segments = pipeline.run(job, on_progress=progress)
    except gemini.BusyError as exc:
        if _schedule_auto_retry(job, exc):
            return
        _fail(job, exc)
        return
    except Exception as exc:  # any failure → refund and show a readable error
        log.exception("Job %s failed", job.pk)
        _update(job, status=Job.Status.FAILED, error=str(exc)[:2000], finished_at=timezone.now(),
                progress_note="Failed — minutes refunded")
        refund_job(job)
        return

    _update(job, segments=segments, partial={}, live_segments=[], auto_retries=0, status=Job.Status.DONE, progress=100, progress_note="Ready",
            finished_at=timezone.now(),
            model_used=f"{settings.GEMINI_TRANSCRIBE_MODEL} / {settings.GEMINI_TRANSLATE_MODEL}")
    _notify_done(job)


AUTO_RETRY_ENABLED = True  # the folder command turns this off and waits in its own loop


def _schedule_auto_retry(job, exc):
    """Google is busy: wait a few minutes and try again by itself (finished parts are kept)."""
    if not AUTO_RETRY_ENABLED or job.auto_retries >= settings.AUTO_RETRY_LIMIT:
        return False
    minutes = settings.AUTO_RETRY_MINUTES
    at = timezone.localtime() + timezone.timedelta(minutes=minutes)
    done = len((job.partial or {}).get("chunks", {}))
    _update(job, status=Job.Status.QUEUED, auto_retries=job.auto_retries + 1,
            progress_note=f"Google is busy — trying again automatically at {at:%H:%M} "
                          f"(attempt {job.auto_retries + 2}; {done} part(s) already done)")
    runner.enqueue(job.pk, delay=minutes * 60)
    log.info("Job %s: Google busy, auto-retry in %s min (%s)", job.pk, minutes, exc)
    return True


def _fail(job, exc):
    _update(job, status=Job.Status.FAILED, error=str(exc)[:2000], finished_at=timezone.now(),
            progress_note="Failed — minutes refunded")
    refund_job(job)


@shared_task(acks_late=True, max_retries=0, time_limit=60 * 60 * 2)
def translate_job(job_id):
    """Add an English translation to a job that was transcribed in Bangla only."""
    job = Job.objects.filter(pk=job_id).first()
    if not job or not job.segments:
        return
    _update(job, status=Job.Status.TRANSLATING, progress=80, progress_note="Translating to English", error="")
    try:
        with gemini.use_key(job.gemini_key_id):
            segments = pipeline.translate_segments(
                job.segments,
                on_progress=lambda d, t: _update(job, progress=80 + int(19 * d / t), progress_note=f"Translating ({d}/{t})"),
            )
    except Exception as exc:
        log.exception("Translation for job %s failed", job.pk)
        for s in job.segments:
            s["en"] = ""
        _update(job, segments=job.segments, status=Job.Status.DONE, progress=100, translate=False,
                progress_note="Ready", error=f"English translation failed: {str(exc)[:500]}")
        return
    _update(job, segments=segments, translate=True, status=Job.Status.DONE, progress=100, progress_note="Ready")


def _notify_done(job):
    if settings.EMAIL_BACKEND.endswith("console.EmailBackend"):
        return
    send_mail(
        f"[{settings.SITE_NAME}] Your transcript is ready: {job.title}",
        f"Hello,\n\nThe transcript for '{job.title}' is ready to download from your dashboard.\n",
        settings.DEFAULT_FROM_EMAIL,
        [job.user.email],
        fail_silently=True,
    )


@shared_task
def purge_expired_audio():
    """Delete audio whose retention period has passed (transcripts are kept)."""
    now = timezone.now()
    due = Job.objects.filter(audio_deleted_at__isnull=True, audio_delete_after__lte=now).exclude(audio_relpath="")
    # don't pull audio out from under a running job unless it is badly overdue
    due = due.filter(Q(status__in=[Job.Status.DONE, Job.Status.FAILED]) |
                     Q(audio_delete_after__lte=now - timezone.timedelta(days=1)))
    count = 0
    for job in due:
        job.delete_audio()
        count += 1
    log.info("Purged audio for %s job(s)", count)
    return count
