import io
from pathlib import Path

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone
from docx import Document

from accounts.models import User
from billing.models import CreditGrant, PaymentRequest, Plan
from billing.services import InsufficientCredits, balance_minutes, refund_job, reserve_minutes
from transcription import runner
from transcription.models import Job
from transcription.tasks import process_job, purge_expired_audio

pytestmark = pytest.mark.django_db


@pytest.fixture
def user():
    return User.objects.create_user("rana@example.com", "pass-12345-word", is_approved=True, full_name="Rana")


def upload_payload(tone_file, **extra):
    data = {
        "audio": io.BytesIO(Path(tone_file).read_bytes()),
        "title": "IDI-01 Garment worker",
        "interview_type": "IDI",
        "retention_days": "1",
        "consent": "on",
        "respondent_id": "R-01",
        "age": "34",
        "location": "Gazipur",
    }
    data["audio"].name = "idi01.mp3"
    data.update(extra)
    return data


# ---------------------------------------------------------------- local mode
def test_local_first_signup_becomes_admin_and_is_approved(client, settings):
    settings.LOCAL_MODE, settings.REQUIRE_APPROVAL, settings.BILLING_ENABLED = True, False, False
    resp = client.post(reverse("signup"), {
        "full_name": "Rana", "email": "redoan@example.com", "password1": "Strong-pass-981", "password2": "Strong-pass-981",
        "accept_terms": "on",
    })
    assert resp.status_code == 302
    u = User.objects.get()
    assert u.is_superuser and u.is_approved
    assert balance_minutes(u) == 0  # no trial credits in local mode


def test_home_redirects_to_dashboard_in_local_mode(client, settings):
    settings.LOCAL_MODE = True
    assert client.get("/").url == reverse("dashboard")


def _docx_text(resp):
    doc = Document(io.BytesIO(b"".join(resp.streaming_content)))
    return doc, "\n".join(p.text for p in doc.paragraphs)


def test_local_upload_bangla_only_skips_translation(client, user, tone_file, fake_gemini, sync_runner, settings,
                                                    django_capture_on_commit_callbacks):
    settings.BILLING_ENABLED, settings.CHUNK_SECONDS = False, 60
    client.force_login(user)
    with django_capture_on_commit_callbacks(execute=True):
        resp = client.post(reverse("upload"), upload_payload(tone_file))
    job = Job.objects.get()
    assert resp.status_code == 302 and resp.url == job.get_absolute_url()
    job.refresh_from_db()
    assert job.status == Job.Status.DONE, job.error
    assert not job.translate and not job.has_english
    assert fake_gemini["translate"] == []  # no translation calls at all
    assert len(fake_gemini["transcribe"]) >= 3  # 200 s recording in 60 s parts
    starts = [s["start"] for s in job.segments]
    assert starts == sorted(starts) and starts[-1] > 100

    page = client.get(job.get_absolute_url()).content.decode()
    assert "প্রশ্ন 1" in page and "Add English translation" in page and "প্রশ্নকারী" in page
    doc, text = _docx_text(client.get(reverse("job_download", args=[job.public_id, "docx"])))
    assert "বাংলা ট্রান্সক্রিপ্ট" in text and "English Translation" not in text
    assert "[00:00:05]" not in text and "উত্তরদাতা: উত্তর 1" in text
    notice = doc.tables[0].rows[0].cells[0].text
    assert "prepared with the help of AI" in notice and "check it manually" in notice  # red box comes first
    assert "FDECEC" in doc.tables[0]._tbl.xml and "C62828" in doc.tables[0]._tbl.xml
    assert doc.element.body[0].tag.endswith("}tbl")  # nothing sits above the box
    assert any(c.text == "Gazipur" for row in doc.tables[1].rows for c in row.cells)
    _, ts_text = _docx_text(client.get(reverse("job_download", args=[job.public_id, "docx-ts"])))
    assert "[০০:০০:০৫]" in ts_text

    # add the English translation afterwards
    client.post(reverse("job_translate", args=[job.public_id]))
    job.refresh_from_db()
    assert job.has_english and job.status == Job.Status.DONE
    _, text = _docx_text(client.get(reverse("job_download", args=[job.public_id, "docx"])))
    assert "English Translation" in text and "Respondent: EN: উত্তর 1" in text


def test_local_upload_with_translation(client, user, tone_file, fake_gemini, sync_runner, settings,
                                       django_capture_on_commit_callbacks):
    settings.BILLING_ENABLED, settings.CHUNK_SECONDS = False, 60
    client.force_login(user)
    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("upload"), upload_payload(tone_file, translate="on"))
    job = Job.objects.get()
    assert job.translate and job.status == Job.Status.DONE and job.has_english
    assert all(s["en"].startswith("EN:") for s in job.segments)
    doc, _ = _docx_text(client.get(reverse("job_download", args=[job.public_id, "docx-table"])))
    assert doc.tables[-1].cell(0, 1).text == "বাংলা"


def test_busy_google_schedules_automatic_retry(client, user, tone_file, monkeypatch, settings,
                                               django_capture_on_commit_callbacks):
    from transcription import gemini

    settings.BILLING_ENABLED, settings.CHUNK_SECONDS = False, 600
    calls, scheduled = [], []

    def busy(*a, **k):
        calls.append(1)
        raise gemini.BusyError("Google's AI servers are busy right now")

    def fake_enqueue(job_id, kind="transcribe", delay=0):
        if delay:
            scheduled.append(delay)
        else:
            process_job(job_id)

    monkeypatch.setattr(gemini, "transcribe_audio_clip", busy)
    monkeypatch.setattr(runner, "enqueue", fake_enqueue)
    client.force_login(user)
    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("upload"), upload_payload(tone_file))
    job = Job.objects.get()
    assert job.status == Job.Status.QUEUED and job.auto_retries == 1
    assert scheduled == [settings.AUTO_RETRY_MINUTES * 60]
    assert "trying again automatically" in job.progress_note


def test_upload_requires_consent(client, user, tone_file, settings):
    settings.BILLING_ENABLED = False
    client.force_login(user)
    data = upload_payload(tone_file)
    data.pop("consent")
    resp = client.post(reverse("upload"), data)
    assert resp.status_code == 200 and not Job.objects.exists()


def test_upload_rejects_unknown_extension(client, user, settings):
    settings.BILLING_ENABLED = False
    client.force_login(user)
    f = io.BytesIO(b"hello")
    f.name = "notes.docx"
    resp = client.post(reverse("upload"), {**upload_payload(Path(__file__)), "audio": f})
    assert b"Unsupported file type" in resp.content


def test_stalled_job_can_be_resumed(client, user, tone_file, fake_gemini, sync_runner, settings, tmp_path):
    settings.BILLING_ENABLED, settings.TASK_RUNNER, settings.CHUNK_SECONDS = False, "thread", 600
    dest = Path(settings.PRIVATE_MEDIA_ROOT) / "1" / "a.mp3"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(Path(tone_file).read_bytes())
    job = Job.objects.create(user=user, title="t", original_filename="a.mp3", audio_relpath="1/a.mp3",
                             duration_seconds=200, billed_minutes=4, status=Job.Status.PROCESSING, progress=40,
                             audio_delete_after=timezone.now() + timezone.timedelta(days=1))
    client.force_login(user)
    assert b"Stopped at" in client.get(job.get_absolute_url()).content
    client.post(reverse("job_resume", args=[job.public_id]))
    job.refresh_from_db()
    assert job.status == Job.Status.DONE


def test_failed_job_shows_error(client, user, tone_file, monkeypatch, sync_runner, settings,
                                django_capture_on_commit_callbacks):
    from transcription import gemini

    settings.BILLING_ENABLED = False

    def boom(*a, **k):
        raise gemini.GeminiError("Gemini API error 403: API key not valid")

    monkeypatch.setattr(gemini, "transcribe_audio_clip", boom)
    client.force_login(user)
    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("upload"), upload_payload(tone_file))
    job = Job.objects.get()
    assert job.status == Job.Status.FAILED and "403" in job.error
    assert b"API key not valid" in client.get(job.get_absolute_url()).content


def test_delete_removes_audio_and_job(client, user, settings):
    path = Path(settings.PRIVATE_MEDIA_ROOT) / "1" / "x.mp3"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"x")
    job = Job.objects.create(user=user, title="t", original_filename="x.mp3", audio_relpath="1/x.mp3",
                             status=Job.Status.DONE)
    client.force_login(user)
    client.post(reverse("job_delete", args=[job.public_id]))
    assert not Job.objects.exists() and not path.exists()


def test_other_users_cannot_see_a_job(client, user):
    other = User.objects.create_user("x@example.com", "pass-12345-word", is_approved=True)
    job = Job.objects.create(user=user, title="secret", original_filename="a.mp3", status=Job.Status.DONE)
    client.force_login(other)
    assert client.get(job.get_absolute_url()).status_code == 404


def test_purge_deletes_only_expired_audio(user, settings):
    root = Path(settings.PRIVATE_MEDIA_ROOT)
    (root / "1").mkdir(parents=True)
    old, new = root / "1" / "old.mp3", root / "1" / "new.mp3"
    old.write_bytes(b"1"), new.write_bytes(b"2")
    now = timezone.now()
    j_old = Job.objects.create(user=user, title="o", original_filename="o", audio_relpath="1/old.mp3",
                               status=Job.Status.DONE, audio_delete_after=now - timezone.timedelta(hours=1))
    Job.objects.create(user=user, title="n", original_filename="n", audio_relpath="1/new.mp3",
                       status=Job.Status.DONE, audio_delete_after=now + timezone.timedelta(days=2))
    assert purge_expired_audio() == 1
    assert not old.exists() and new.exists()
    j_old.refresh_from_db()
    assert j_old.audio_deleted_at is not None


def test_transcribe_folder_command(user, tone_file, fake_gemini, tmp_path, settings):
    user.is_superuser = True
    user.save()
    settings.CHUNK_SECONDS = 600
    folder = tmp_path / "interviews"
    folder.mkdir()
    (folder / "KII 03.mp3").write_bytes(Path(tone_file).read_bytes())
    (folder / "readme.txt").write_text("ignore me")
    call_command("transcribe_folder", str(folder), "--type", "KII")
    out = folder / "transcripts"
    assert (out / "kii-03.docx").exists() and fake_gemini["translate"] == []
    assert not list(out.glob("*.txt"))  # Word only
    assert (folder / "KII 03.mp3").exists()  # original untouched
    assert not any(Path(settings.PRIVATE_MEDIA_ROOT).rglob("*.mp3"))  # private copy removed
    call_command("transcribe_folder", str(folder))  # second run skips
    assert Job.objects.count() == 1


# ---------------------------------------------------------------- website (saas) mode
def test_unapproved_user_cannot_upload(client, settings):
    settings.REQUIRE_APPROVAL = True
    u = User.objects.create_user("new@example.com", "pass-12345-word")
    client.force_login(u)
    assert client.get(reverse("upload")).url == reverse("dashboard")


def test_credits_are_spent_soonest_expiring_first_and_refunded(user):
    now = timezone.now()
    later = CreditGrant.objects.create(user=user, minutes=100, remaining=100, expires_at=now + timezone.timedelta(days=30))
    soon = CreditGrant.objects.create(user=user, minutes=20, remaining=20, expires_at=now + timezone.timedelta(days=2))
    job = Job.objects.create(user=user, title="t", original_filename="a")
    reserve_minutes(user, 50, job)
    soon.refresh_from_db(), later.refresh_from_db()
    assert (soon.remaining, later.remaining) == (0, 70)
    with pytest.raises(InsufficientCredits):
        reserve_minutes(user, 71, job)
    assert refund_job(job) == 50 and refund_job(job) == 0
    assert balance_minutes(user) == 120


def test_upload_blocked_without_credits_when_billing_on(client, user, tone_file, settings):
    settings.BILLING_ENABLED = True
    client.force_login(user)
    resp = client.post(reverse("upload"), upload_payload(tone_file))
    assert resp.url == reverse("buy_credits") and not Job.objects.exists()
    assert not any(Path(settings.PRIVATE_MEDIA_ROOT).rglob("*.mp3"))


def test_bkash_payment_flow(client, user, settings):
    settings.BILLING_ENABLED = True
    plan = Plan.objects.get(name="Starter")
    client.force_login(user)
    client.post(reverse("buy_credits"), {"plan": plan.pk, "sender_number": "01712-345678", "trx_id": "9ab1cd2efg"})
    payment = PaymentRequest.objects.get()
    assert payment.trx_id == "9AB1CD2EFG" and payment.amount_bdt == plan.price_bdt
    resp = client.post(reverse("buy_credits"), {"plan": plan.pk, "sender_number": "01712345678", "trx_id": "9AB1CD2EFG"})
    assert b"already been submitted" in resp.content
    payment.approve()
    assert balance_minutes(user) == plan.minutes
    assert payment.approve() is False  # no double credit


def test_public_pages_render_in_saas_mode(client, settings):
    settings.LOCAL_MODE = False
    for name in ("home", "pricing", "privacy", "terms", "login", "signup"):
        assert client.get(reverse(name)).status_code == 200, name


def test_retry_reuses_finished_parts(client, user, tone_file, monkeypatch, sync_runner, settings,
                                     django_capture_on_commit_callbacks):
    """Part 1 succeeds, part 2 fails; on 'Try again' only part 2 (and later) is sent."""
    from transcription import gemini

    settings.BILLING_ENABLED, settings.CHUNK_SECONDS = False, 60
    sent = []

    def flaky(audio_bytes, prompt, mime_type="audio/mp3", on_segments=None):
        sent.append(prompt)
        if len(sent) == 2:
            raise gemini.GeminiError("503 busy")
        return [{"start": "00:01", "speaker": "Respondent", "text": f"অংশ {len(sent)}", "uncertain": False}]

    monkeypatch.setattr(gemini, "transcribe_audio_clip", flaky)
    monkeypatch.setattr(gemini, "translate_batch", lambda b: {s["i"]: "en" for s in b})
    client.force_login(user)
    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("upload"), upload_payload(tone_file))
    job = Job.objects.get()
    assert job.status == Job.Status.FAILED and len(job.partial["chunks"]) == 1
    calls_before = len(sent)
    client.post(reverse("job_resume", args=[job.public_id]))
    job.refresh_from_db()
    assert job.status == Job.Status.DONE and job.partial == {}
    n_chunks = len(job.segments)
    assert len(sent) - calls_before == n_chunks - 1  # part 1 was not sent again


def test_live_text_and_partial_word_when_it_stops(client, user, tone_file, monkeypatch, sync_runner, settings,
                                                  django_capture_on_commit_callbacks):
    """Part 1 finishes, part 2 streams two lines then Google gives up: the page shows where it
    stopped, the live text is kept, and 'what's done so far' downloads as Word."""
    from transcription import gemini

    settings.BILLING_ENABLED, settings.CHUNK_SECONDS = False, 60
    calls = []

    def flaky(audio_bytes, prompt, mime_type="audio/mp3", on_segments=None):
        calls.append(1)
        n = len(calls)
        segs = [{"start": "00:02", "speaker": "Interviewer", "text": f"প্রশ্ন {n}", "uncertain": False},
                {"start": "00:20", "speaker": "Respondent", "text": f"উত্তর {n}", "uncertain": False}]
        if n == 2:
            on_segments(segs)
            raise gemini.DailyLimitError("The free Gemini quota for today is used up.")
        return segs

    monkeypatch.setattr(gemini, "transcribe_audio_clip", flaky)
    client.force_login(user)
    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("upload"), upload_payload(tone_file))
    job = Job.objects.get()
    assert job.status == Job.Status.FAILED
    assert [s["bn"] for s in job.available_segments()] == ["প্রশ্ন 1", "উত্তর 1", "প্রশ্ন 2", "উত্তর 2"]
    second_start = float(min(job.partial["ends"].values()))
    assert abs(job.covered_seconds - (second_start + 20)) < 1

    page = client.get(job.get_absolute_url()).content.decode()
    assert f"Stopped at {job.covered_display}" in page and "উত্তর 2" in page and "done so far" in page

    status = client.get(reverse("job_status", args=[job.public_id])).json()
    assert status["count"] == 4 and status["turns"][1]["who"] == "উত্তরদাতা"

    resp = client.get(reverse("job_download", args=[job.public_id, "partial"]))
    doc, text = _docx_text(resp)
    assert "Incomplete" in text and job.covered_display in text and "উত্তর 2" in text
    job.refresh_from_db()
    assert job.status == Job.Status.FAILED and job.segments == []  # the download did not change the job


def test_folder_command_saves_partial_and_continues_next_run(user, tone_file, monkeypatch, tmp_path, settings):
    from transcription import gemini

    user.is_superuser = True
    user.save()
    settings.CHUNK_SECONDS = 60
    folder = tmp_path / "interviews"
    folder.mkdir()
    (folder / "IDI 05.mp3").write_bytes(Path(tone_file).read_bytes())
    calls, limit = [], {"stop_at": 2}

    def flaky(audio_bytes, prompt, mime_type="audio/mp3", on_segments=None):
        calls.append(1)
        if len(calls) == limit["stop_at"]:
            raise gemini.DailyLimitError("quota used up")
        return [{"start": "00:01", "speaker": "Respondent", "text": f"অংশ {len(calls)}", "uncertain": False}]

    monkeypatch.setattr(gemini, "transcribe_audio_clip", flaky)
    call_command("transcribe_folder", str(folder))
    out = folder / "transcripts"
    assert (out / "idi-05-partial.docx").exists() and not (out / "idi-05.docx").exists()

    limit["stop_at"] = -1
    before = len(calls)
    call_command("transcribe_folder", str(folder))
    assert (out / "idi-05.docx").exists() and not (out / "idi-05-partial.docx").exists()
    n_parts = len(Job.objects.get(status=Job.Status.DONE).segments)
    assert len(calls) - before == n_parts - 1  # the finished first part was not sent again
    assert Job.objects.count() == 1


def test_changing_part_length_keeps_finished_work(client, user, tone_file, monkeypatch, sync_runner, settings,
                                                  django_capture_on_commit_callbacks):
    """A job that stopped with 60-second parts continues with 60-second parts even after the
    setting is raised, so no finished part is sent again."""
    from transcription import gemini

    settings.BILLING_ENABLED, settings.CHUNK_SECONDS = False, 60
    sent = []

    def flaky(audio_bytes, prompt, mime_type="audio/mp3", on_segments=None):
        sent.append(1)
        if len(sent) == 2:
            raise gemini.DailyLimitError("quota used up")
        return [{"start": "00:01", "speaker": "Respondent", "text": f"অংশ {len(sent)}"}]

    monkeypatch.setattr(gemini, "transcribe_audio_clip", flaky)
    client.force_login(user)
    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("upload"), upload_payload(tone_file))
    job = Job.objects.get()
    assert job.status == Job.Status.FAILED and len(job.partial["chunks"]) == 1

    settings.CHUNK_SECONDS = 1200
    before = len(sent)
    client.post(reverse("job_resume", args=[job.public_id]))
    job.refresh_from_db()
    assert job.status == Job.Status.DONE and job.segments[0]["bn"] == "অংশ 1"
    assert len(sent) - before == len(job.segments) - 1

