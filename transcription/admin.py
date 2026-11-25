from django.contrib import admin, messages

from billing.services import refund_job

from .models import Job
from . import runner


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "user", "interview_type", "duration_display", "billed_minutes",
                    "status", "progress", "created_at", "audio_deleted_at")
    list_filter = ("status", "interview_type")
    search_fields = ("title", "user__email", "original_filename")
    # Transcript text is deliberately not shown in admin, to keep participant data private.
    exclude = ("segments",)
    readonly_fields = ("public_id", "user", "original_filename", "audio_relpath", "file_size", "duration_seconds",
                       "billed_minutes", "consent_confirmed", "created_at", "started_at", "finished_at",
                       "audio_delete_after", "audio_deleted_at", "model_used", "error", "progress_note")
    actions = ["retry_jobs", "refund_jobs"]

    @admin.action(description="Retry failed job(s) (re-charges minutes)")
    def retry_jobs(self, request, queryset):
        from billing.services import InsufficientCredits, reserve_minutes

        n = 0
        for job in queryset.filter(status=Job.Status.FAILED):
            if not job.has_audio:
                continue
            try:
                reserve_minutes(job.user, job.billed_minutes, job)
            except InsufficientCredits:
                continue
            job.status, job.progress, job.error = Job.Status.QUEUED, 0, ""
            job.save(update_fields=["status", "progress", "error"])
            runner.enqueue(job.pk)
            n += 1
        self.message_user(request, f"Re-queued {n} job(s).", messages.SUCCESS)

    @admin.action(description="Refund minutes for selected job(s)")
    def refund_jobs(self, request, queryset):
        total = sum(refund_job(job) for job in queryset)
        self.message_user(request, f"Refunded {total} minute(s).", messages.SUCCESS)
