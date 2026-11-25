"""Transcribe every recording in a folder, without the browser.

Example (Windows):
    python manage.py transcribe_folder "D:\\Interviews\\Round1" --type IDI

A Word file per recording is written to <folder>\\transcripts (or --out). If a recording
stops half-way, what is done is saved as <name>-partial.docx and the next run continues it.
Files that already have a Word report there are skipped, so you can stop and
re-run the command safely. Your original audio files are never changed or deleted.
"""
import shutil
import time
import uuid
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.text import slugify

from accounts.models import User
from transcription import audio, report
from transcription.models import Job
from transcription import tasks
from transcription.tasks import process_job


class Command(BaseCommand):
    help = "Transcribe all audio files in a folder and save Word + text files next to them."

    def add_arguments(self, parser):
        parser.add_argument("folder")
        parser.add_argument("--out", help="Output folder (default: <folder>/transcripts)")
        parser.add_argument("--type", default="IDI", choices=[c for c, _ in Job.InterviewType.choices])
        parser.add_argument("--context", default="", help="Background for the AI: topic, place names, terms")
        parser.add_argument("--speakers", type=int, default=None, help="Expected number of speakers")
        parser.add_argument("--user", help="Email of the account to save jobs under (default: first admin)")
        parser.add_argument("--recursive", action="store_true", help="Also look inside sub-folders")
        parser.add_argument("--translate", action="store_true", help="Also make the English translation")
        parser.add_argument("--timestamps", action="store_true", help="Keep timestamps in the Word file")

    def handle(self, *args, **opts):
        tasks.AUTO_RETRY_ENABLED = False  # this command waits and retries in its own loop
        if not settings.GEMINI_API_KEY:
            raise CommandError("GEMINI_API_KEY is empty. Put your key in the .env file first.")
        folder = Path(opts["folder"]).expanduser()
        if not folder.is_dir():
            raise CommandError(f"Folder not found: {folder}")
        out = Path(opts["out"]) if opts["out"] else folder / "transcripts"
        out.mkdir(parents=True, exist_ok=True)

        user = (User.objects.filter(email__iexact=opts["user"]).first() if opts["user"]
                else User.objects.filter(is_superuser=True).order_by("id").first())
        if not user:
            raise CommandError("No account found. Open the app once and create an account first.")

        pattern = "**/*" if opts["recursive"] else "*"
        files = sorted(p for p in folder.glob(pattern)
                       if p.is_file() and p.suffix.lower() in settings.ALLOWED_AUDIO_EXTENSIONS
                       and out not in p.parents)
        if not files:
            raise CommandError("No audio files found in that folder.")
        self.stdout.write(f"Found {len(files)} recording(s). Output → {out}\n")

        done = failed = skipped = 0
        for n, src in enumerate(files, 1):
            base = slugify(src.stem, allow_unicode=True)[:80] or f"recording-{n}"
            target = out / f"{base}.docx"
            if target.exists():
                skipped += 1
                self.stdout.write(f"[{n}/{len(files)}] skip (already done): {src.name}")
                continue
            self.stdout.write(f"[{n}/{len(files)}] {src.name} …")
            try:
                duration = audio.probe_duration(src)
            except audio.AudioError as exc:
                failed += 1
                self.stderr.write(f"    cannot read: {exc}")
                continue

            # work on a private copy so the original file is never touched
            rel = Path(str(user.pk)) / f"{uuid.uuid4().hex}{src.suffix.lower()}"
            dest = Path(settings.PRIVATE_MEDIA_ROOT) / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            # a previous run that stopped half-way on this file? reuse its finished parts
            leftovers = Job.objects.filter(user=user, original_filename=src.name, source="folder").exclude(
                status=Job.Status.DONE)
            earlier = leftovers.order_by("-created_at").first()
            job = Job.objects.create(
                user=user, title=src.stem, interview_type=opts["type"], expected_speakers=opts["speakers"],
                context_notes=opts["context"], original_filename=src.name, audio_relpath=str(rel),
                file_size=src.stat().st_size, duration_seconds=duration, billed_minutes=Job.minutes_for(duration),
                consent_confirmed=True, retention_days=1, audio_delete_after=timezone.now(),
                translate=opts["translate"], source="folder",
            )
            if earlier and abs(earlier.duration_seconds - duration) < 1 and earlier.partial:
                job.partial, job.live_segments = earlier.partial, earlier.live_segments
                job.save(update_fields=["partial", "live_segments"])
                self.stdout.write(f"    continuing from {earlier.covered_display} (saved last time)")
            leftovers.exclude(pk=job.pk).delete()
            for attempt in range(settings.AUTO_RETRY_LIMIT + 1):
                process_job(job.pk)
                job.refresh_from_db()
                busy = job.status == Job.Status.FAILED and "busy" in job.error.lower()
                if not busy or attempt == settings.AUTO_RETRY_LIMIT:
                    break
                done_parts = len((job.partial or {}).get("chunks", {}))
                self.stdout.write(f"    Google is busy ({done_parts} part(s) done). Waiting "
                                  f"{settings.AUTO_RETRY_MINUTES} min, then continuing…")
                time.sleep(settings.AUTO_RETRY_MINUTES * 60)
                Job.objects.filter(pk=job.pk).update(status=Job.Status.QUEUED, error="")
            job.delete_audio()  # remove our copy right away
            partial_file = out / f"{base}-partial.docx"
            if job.status != Job.Status.DONE:
                failed += 1
                self.stderr.write(f"    STOPPED: {job.error[:300]}")
                segs = job.available_segments()
                if segs:
                    note = (f"Incomplete: covers about 0:00 to {job.covered_display} of {job.duration_display}. "
                            "Run the folder again later to finish it.")
                    job.segments = [dict(s, en="") for s in segs]
                    partial_file.write_bytes(report.build_docx(job, "clean", partial_note=note).getvalue())
                    self.stderr.write(f"    Saved what is done (up to {job.covered_display}) → {partial_file.name}")
                continue
            layout = "timestamps" if opts["timestamps"] else "clean"
            target.write_bytes(report.build_docx(job, layout).getvalue())
            partial_file.unlink(missing_ok=True)
            done += 1
            flag = f", {job.uncertain_count} unclear segment(s) to check" if job.uncertain_count else ""
            self.stdout.write(self.style.SUCCESS(f"    done ({job.duration_display}{flag})"))

        self.stdout.write(f"\nFinished: {done} done, {skipped} skipped, {failed} failed.")
