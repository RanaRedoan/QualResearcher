import uuid
from functools import wraps
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import FileResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from billing.services import InsufficientCredits, balance_minutes, reserve_minutes

from . import audio, report, runner
from .pipeline import format_timestamp
from .report import bangla_label
from .forms import UploadForm
from .models import Job
from .tasks import purge_expired_audio


def approved_required(view):
    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not request.user.is_approved:
            messages.info(request, "Your account is waiting for approval. We'll activate it shortly.")
            return redirect("dashboard")
        return view(request, *args, **kwargs)

    return wrapper


def _is_stalled(job):
    """Thread runner only: a job that is unfinished but not in this process's queue
    (e.g. the PC was restarted mid-job) can be resumed by the user."""
    return (settings.TASK_RUNNER == "thread" and not job.is_finished
            and not runner.is_alive_here(job.pk))


@login_required
def dashboard(request):
    if settings.TASK_RUNNER == "thread":
        purge_expired_audio()  # no scheduler on a local PC, so tidy up here
    jobs = list(request.user.jobs.all()[:200])
    for job in jobs:
        job.stalled = _is_stalled(job)
    return render(request, "transcription/dashboard.html", {
        "jobs": jobs,
        "balance": balance_minutes(request.user) if settings.BILLING_ENABLED else None,
        "active_count": sum(1 for j in jobs if not j.is_finished),
        "done_count": sum(1 for j in jobs if j.status == Job.Status.DONE),
        "total_minutes": sum(j.billed_minutes for j in jobs if j.status == Job.Status.DONE),
    })


def _save_upload(user, uploaded) -> Path:
    rel = Path(str(user.pk)) / f"{uuid.uuid4().hex}{Path(uploaded.name).suffix.lower()}"
    dest = Path(settings.PRIVATE_MEDIA_ROOT) / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as out:
        for part in uploaded.chunks():
            out.write(part)
    return rel


@approved_required
def upload(request):
    form = UploadForm(request.POST or None, request.FILES or None)
    balance = balance_minutes(request.user) if settings.BILLING_ENABLED else None
    if request.method == "POST" and form.is_valid():
        uploaded = form.cleaned_data["audio"]
        rel = _save_upload(request.user, uploaded)
        path = Path(settings.PRIVATE_MEDIA_ROOT) / rel
        try:
            duration = audio.probe_duration(path)
        except audio.AudioError as exc:
            path.unlink(missing_ok=True)
            form.add_error("audio", str(exc))
            return render(request, "transcription/upload.html", {"form": form, "balance": balance})

        cd = form.cleaned_data
        try:
            with transaction.atomic():
                job = Job.objects.create(
                    user=request.user,
                    title=cd["title"],
                    interview_type=cd["interview_type"],
                    expected_speakers=cd.get("expected_speakers"),
                    context_notes=cd.get("context_notes", ""),
                    demographics=form.demographics(),
                    original_filename=Path(uploaded.name).name[:255],
                    audio_relpath=str(rel),
                    file_size=uploaded.size,
                    duration_seconds=duration,
                    billed_minutes=Job.minutes_for(duration),
                    consent_confirmed=True,
                    translate=cd.get("translate", False),
                    retention_days=cd["retention_days"],
                    gemini_key_id=cd.get("gemini_key") or "",
                    audio_delete_after=timezone.now() + timezone.timedelta(days=cd["retention_days"]),
                )
                if settings.BILLING_ENABLED:
                    reserve_minutes(request.user, job.billed_minutes, job)
                transaction.on_commit(lambda: runner.enqueue(job.pk))
        except InsufficientCredits as exc:
            path.unlink(missing_ok=True)
            messages.error(
                request,
                f"This recording is {Job.minutes_for(duration)} minutes but you have {exc.available} minutes left. "
                "Please buy more credits.",
            )
            return redirect(reverse("buy_credits"))
        if settings.BILLING_ENABLED:
            messages.success(request, f"Uploaded. {job.billed_minutes} minutes reserved — we're on it.")
        else:
            messages.success(request, f"Uploaded ({job.duration_display}). Transcription has started.")
        return redirect(job)
    return render(request, "transcription/upload.html", {"form": form, "balance": balance})


def _get_job(request, public_id):
    return get_object_or_404(Job, public_id=public_id, user=request.user)


@login_required
def job_detail(request, public_id):
    job = _get_job(request, public_id)
    job.stalled = _is_stalled(job)
    ctx = {"job": job}
    if settings.DESKTOP:
        from desktop import store

        ctx["gemini_keys"] = store.keys()
        ctx["job_key_name"] = store.key_name(job.gemini_key_id)
        ctx["job_key_id"] = (store.get_key(job.gemini_key_id) or {}).get("id", "")
        ctx["export_names"] = {k.replace("-", "_"): export_filename(job, k)
                               for k in ("docx", "docx-ts", "docx-table", "partial")}
    return render(request, "transcription/job_detail.html", ctx)


@login_required
def job_status(request, public_id):
    """Polled every few seconds by the job page; includes the transcript text so far."""
    job = _get_job(request, public_id)
    segs = job.available_segments()
    return JsonResponse({
        "status": job.status, "label": job.get_status_display(), "progress": job.progress,
        "note": job.progress_note, "finished": job.is_finished, "stalled": _is_stalled(job),
        "covered": job.covered_display, "total": job.duration_display,
        "count": len(segs), "chars": sum(len(s["bn"]) for s in segs),
        "turns": [{"who": bangla_label(s["speaker"]), "t": format_timestamp(s["start"]), "text": s["bn"],
                   "q": s["speaker"] in ("Interviewer", "Moderator"), "u": bool(s.get("uncertain"))}
                  for s in segs],
    })


@login_required
@require_POST
def resume_job(request, public_id):
    """Restart a job that stopped (PC restart) or failed, using the audio still on disk."""
    job = _get_job(request, public_id)
    if not (job.status == Job.Status.FAILED or _is_stalled(job)):
        return redirect(job)
    if not job.has_audio:
        messages.error(request, "The audio for this job has already been deleted. Please upload it again.")
        return redirect(job)
    if settings.BILLING_ENABLED and job.status == Job.Status.FAILED:
        try:
            reserve_minutes(request.user, job.billed_minutes, job)
        except InsufficientCredits:
            messages.error(request, "Not enough minutes to retry this job.")
            return redirect("buy_credits")
    job.status, job.progress, job.error, job.progress_note = Job.Status.QUEUED, 0, "", "Waiting to start"
    job.auto_retries = 0
    fields = ["status", "progress", "error", "progress_note", "auto_retries"]
    new_key = request.POST.get("gemini_key", "")
    if settings.DESKTOP and new_key:
        from desktop import store

        if store.get_key(new_key) and new_key != job.gemini_key_id:
            job.gemini_key_id = new_key
            fields.append("gemini_key_id")
    job.save(update_fields=fields)
    runner.enqueue(job.pk)
    messages.success(request, "Restarted.")
    return redirect(job)


def _docx_response(buf, filename):
    return FileResponse(
        buf, as_attachment=True, filename=filename,
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


DOCX_KINDS = {"docx": ("clean", ""), "docx-ts": ("timestamps", "-timestamps"),
              "docx-table": ("table", "-side-by-side")}


def export_filename(job, kind):
    base = slugify(job.title, allow_unicode=True)[:80] or f"transcript-{job.pk}"
    if kind == "partial":
        return f"{base}-partial-{job.covered_display.replace(':', '-')}.docx"
    suffix = DOCX_KINDS.get(kind, ("", ""))[1]
    if kind == "docx-table" and not job.has_english:
        suffix = ""
    return f"{base}{suffix}.docx"


def export_file(job, kind):
    """(BytesIO, filename) for a Word export of `job`, or None if that kind is not available yet."""
    filename = export_filename(job, kind)
    if kind == "partial":
        # whatever is transcribed so far — so a stopped job is still useful
        segs = job.available_segments()
        if job.status == Job.Status.DONE or not segs:
            return None
        note = (f"Incomplete: covers about 0:00 to {job.covered_display} of {job.duration_display}. "
                f"The rest has not been transcribed yet.")
        job.segments = [dict(s, en="") for s in segs]  # in memory only, not saved
        return report.build_docx(job, "clean", partial_note=note), filename
    if job.status != Job.Status.DONE or kind not in DOCX_KINDS:
        return None
    layout = DOCX_KINDS[kind][0]
    if layout == "table" and not job.has_english:
        layout = "clean"
    return report.build_docx(job, layout), filename


@login_required
def download(request, public_id, kind):
    job = _get_job(request, public_id)
    result = export_file(job, kind)
    if result is None:
        if kind != "partial" and job.status != Job.Status.DONE:
            messages.error(request, "This transcript is not ready yet.")
        return redirect(job)
    return _docx_response(*result)


@login_required
@require_POST
def translate_job(request, public_id):
    """Add the English translation to a finished Bangla-only transcript."""
    job = _get_job(request, public_id)
    if job.status != Job.Status.DONE or job.has_english:
        return redirect(job)
    job.status, job.progress, job.progress_note, job.error = Job.Status.TRANSLATING, 80, "Waiting to translate", ""
    job.save(update_fields=["status", "progress", "progress_note", "error"])
    runner.enqueue(job.pk, kind="translate")
    messages.success(request, "Translating to English — this page will update by itself.")
    return redirect(job)


@login_required
@require_POST
def delete_job(request, public_id):
    job = _get_job(request, public_id)
    if not job.is_finished and not _is_stalled(job):
        messages.error(request, "Please wait until processing finishes before deleting.")
        return redirect(job)
    title = job.title
    job.delete()
    messages.success(request, f"Deleted '{title}' — audio and transcript are permanently removed.")
    return redirect("dashboard")
