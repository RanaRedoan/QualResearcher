"""The installed Windows app (APP_MODE=desktop): no login, key setup, app layout, Save as Word."""
import io
from pathlib import Path

import pytest
from django.urls import reverse

from desktop import store, views as dviews
from transcription.models import Job

pytestmark = pytest.mark.django_db

KEY = "AIza" + "x" * 35


@pytest.fixture
def desktop(settings, tmp_path, monkeypatch):
    settings.DESKTOP, settings.LOCAL_MODE = True, True
    settings.BILLING_ENABLED = settings.REQUIRE_APPROVAL = False
    settings.DATA_DIR = tmp_path / "data"
    settings.DATA_DIR.mkdir()
    settings.DEFAULT_EXPORT_DIR = tmp_path / "Documents" / "QualResearcher"
    settings.GEMINI_API_KEY = ""
    settings.GITHUB_REPO = ""
    opened = []
    monkeypatch.setattr(dviews, "_open", lambda p: opened.append(Path(p)))
    monkeypatch.setattr(dviews, "_reveal", lambda p: opened.append(("reveal", Path(p))))
    return opened


@pytest.fixture
def with_key(desktop):
    store.save(name="Rana")
    store.add_key("redoan630", KEY)
    return desktop


def test_first_run_goes_to_setup_without_login(client, desktop):
    resp = client.get(reverse("dashboard"))
    assert resp.status_code == 302 and resp.url == reverse("desktop_setup")
    page = client.get(reverse("desktop_setup")).content.decode()
    assert "aistudio.google.com/apikey" in page and "billing set up" in page
    assert "Log in" not in page  # nobody signs in to the app


def test_setup_saves_a_working_key(client, desktop, monkeypatch):
    monkeypatch.setattr(dviews, "check_key", lambda k: (True, "The key works."))
    resp = client.post(reverse("desktop_setup"), {"name": "Rana", "gemini_api_key": KEY, "key_name": "redoan630"})
    assert resp.status_code == 302 and resp.url == reverse("upload")
    assert store.api_key() == KEY and store.key_name() == "redoan630"
    assert client.get(reverse("dashboard")).status_code == 200


def test_setup_rejects_a_bad_key(client, desktop, monkeypatch):
    monkeypatch.setattr(dviews, "check_key", lambda k: (False, "Google says this key is not valid."))
    page = client.post(reverse("desktop_setup"), {"gemini_api_key": KEY}).content.decode()
    assert "not valid" in page and store.keys() == []


def test_key_format_is_checked_before_calling_google():
    ok, msg = dviews.check_key("hello")
    assert not ok and "Copy it again" in msg


def test_app_layout_and_no_website_pages(client, with_key):
    page = client.get(reverse("dashboard")).content.decode()
    assert 'class="app"' in page and "statusbar" in page and "Default key: redoan630" in page
    assert "Start your first transcript" in page
    for url in ("/", "/pricing/", reverse("signup"), reverse("login")):
        assert client.get(url).url == reverse("dashboard")


def test_gemini_uses_the_key_from_the_app(with_key):
    from transcription import gemini

    seen = {}

    class FakeClient:
        def __init__(self, api_key):
            seen["key"] = api_key

    import google.genai as genai
    orig = genai.Client
    genai.Client = FakeClient
    try:
        gemini._client()
    finally:
        genai.Client = orig
    assert seen["key"] == KEY


def _done_job(user):
    segs = [{"start": 0.0, "speaker": "Interviewer", "bn": "প্রশ্ন", "en": "Question"},
            {"start": 5.0, "speaker": "Respondent", "bn": "উত্তর", "en": "Answer"}]
    return Job.objects.create(user=user, title="IDI-07 Garment worker", status="done", progress=100,
                              duration_seconds=60, segments=segs, translate=True, consent_confirmed=True)


def test_save_as_word_writes_file_and_opens_it(client, with_key, settings):
    client.get(reverse("dashboard"))  # signs the local user in
    from desktop.middleware import local_user
    job = _done_job(local_user())
    page = client.get(job.get_absolute_url()).content.decode()
    assert "Save as Word" in page and reverse("desktop_save", args=[job.public_id, "docx"]) in page
    assert reverse("job_download", args=[job.public_id, "docx"]) not in page

    resp = client.post(reverse("desktop_save", args=[job.public_id, "docx"]))
    assert resp.status_code == 302
    saved = settings.DEFAULT_EXPORT_DIR / "idi-07-garment-worker.docx"
    assert saved.exists() and with_key == [saved]
    # a second save never overwrites the first
    client.post(reverse("desktop_save", args=[job.public_id, "docx"]), {"open": "0"})
    assert (settings.DEFAULT_EXPORT_DIR / "idi-07-garment-worker (2).docx").exists()

    page = client.get(job.get_absolute_url()).content.decode()
    assert "Show in folder" in page and "idi-07-garment-worker (2).docx" in page
    client.post(reverse("desktop_reveal"), {"what": "file", "next": job.get_absolute_url()})
    assert with_key[-1][0] == "reveal"


def test_settings_change_folder_name_and_words(client, with_key, tmp_path):
    other = tmp_path / "Elsewhere"
    resp = client.post(reverse("desktop_settings"), {"name": "R. Redoan", "export_dir": str(other),
                                                     "english_words": "latin"})
    assert resp.status_code == 302
    cfg = store.load()
    assert cfg["export_dir"] == str(other) and cfg["english_words"] == "latin" and store.api_key() == KEY
    from transcription import gemini
    assert "Latin script" in gemini.english_rule()


KEY2 = "AIza" + "y" * 35


def test_named_keys_add_default_delete(client, with_key, monkeypatch):
    monkeypatch.setattr(dviews, "check_key", lambda k: (True, "The key works."))
    client.post(reverse("desktop_key_add"), {"key_name": "The Rana", "gemini_api_key": KEY2})
    names = [k["name"] for k in store.keys()]
    assert names == ["redoan630", "The Rana"] and store.api_key() == KEY  # first key stays default
    page = client.get(reverse("desktop_settings")).content.decode()
    assert "The Rana" in page and "Default" in page and KEY2 not in page  # keys are shown masked
    second = store.keys()[1]["id"]
    client.post(reverse("desktop_key_action", args=[second]), {"action": "default"})
    assert store.api_key() == KEY2
    first = store.keys()[0]["id"]
    client.post(reverse("desktop_key_action", args=[first]), {"action": "delete"})
    assert [k["name"] for k in store.keys()] == ["The Rana"]
    # the last key cannot be removed
    client.post(reverse("desktop_key_action", args=[second]), {"action": "delete"})
    assert len(store.keys()) == 1


def test_bad_key_is_not_added(client, with_key, monkeypatch):
    monkeypatch.setattr(dviews, "check_key", lambda k: (False, "Google says this key is not valid."))
    client.post(reverse("desktop_key_add"), {"key_name": "x", "gemini_api_key": KEY2})
    assert len(store.keys()) == 1


def test_old_single_key_config_still_works(desktop, settings):
    (settings.DATA_DIR / "config.json").write_text('{"gemini_api_key": "%s", "name": "Rana"}' % KEY, encoding="utf-8")
    assert store.api_key() == KEY and store.key_name() == "My key"


def test_each_job_uses_its_own_key(client, with_key, monkeypatch, tone_file, fake_gemini, sync_runner,
                                   django_capture_on_commit_callbacks):
    monkeypatch.setattr(dviews, "check_key", lambda k: (True, "ok"))
    client.post(reverse("desktop_key_add"), {"key_name": "The Rana", "gemini_api_key": KEY2})
    second = store.keys()[1]["id"]
    page = client.get(reverse("upload")).content.decode()
    assert "Key" in page and "The Rana" in page and 'name="gemini_key"' in page

    from transcription import gemini
    used, fake = [], gemini.transcribe_audio_clip

    def spy(*args, **kwargs):  # which key would the real call use right now?
        used.append(store.api_key(getattr(gemini._job_key, "id", "")))
        return fake(*args, **kwargs)

    monkeypatch.setattr(gemini, "transcribe_audio_clip", spy)
    from tests.test_app import upload_payload
    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("upload"), upload_payload(tone_file, gemini_key=second))
    job = Job.objects.get()
    assert job.gemini_key_id == second and job.status == "done", job.error
    assert used and set(used) == {KEY2}  # not the default key
    assert "Gemini key: The Rana" in client.get(job.get_absolute_url()).content.decode()


def test_jobs_json_and_update_check(client, with_key, settings, monkeypatch):
    client.get(reverse("dashboard"))
    from desktop.middleware import local_user
    _done_job(local_user())
    data = client.get(reverse("desktop_jobs")).json()
    assert data["jobs"][0]["status"] == "done" and data["running"] == 0

    settings.GITHUB_REPO = "someone/QualResearcher"
    settings.APP_VERSION = "1.0.0"
    dviews._update_cache.clear()

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(dviews.urllib.request, "urlopen", lambda req, timeout: Resp(
        b'{"tag_name": "v1.2.0", "html_url": "https://github.com/someone/QualResearcher/releases/tag/v1.2.0"}'))
    d = client.get(reverse("desktop_update")).json()
    assert d == {"update": True, "version": "1.2.0",
                 "url": "https://github.com/someone/QualResearcher/releases/tag/v1.2.0"}
    dviews._update_cache.clear()


def test_website_mode_is_unchanged(client, settings):
    settings.DESKTOP = False
    page = client.get(reverse("login")).content.decode()
    assert 'class="app"' not in page and "site-header" in page


def test_key_guide_opens_before_and_after_setup(client, desktop):
    setup = client.get(reverse("desktop_setup")).content.decode()
    assert reverse("desktop_help_key") in setup
    page = client.get(reverse("desktop_help_key"))
    assert page.status_code == 200  # reachable even though no key is saved yet
    text = page.content.decode()
    assert "Gemini API key কীভাবে পাবেন" in text and "aistudio.google.com/apikey" in text
    assert "billing" in text and 'class="app"' not in text
    store.add_key("redoan630", KEY)
    text = client.get(reverse("desktop_help_key")).content.decode()
    assert 'class="app"' in text and "Help: Gemini key" in text


# ---------------------------------------------------------------- 1.0.2: Save As, notifications, beta
def test_save_as_chosen_path_and_folder_is_remembered(client, with_key, tmp_path, settings):
    client.get(reverse("dashboard"))
    from desktop.middleware import local_user
    job = _done_job(local_user())
    page = client.get(job.get_absolute_url()).content.decode()
    assert 'data-save-name="idi-07-garment-worker.docx"' in page
    assert 'data-save-name="idi-07-garment-worker-timestamps.docx"' in page
    assert "Made with AI: check it before you use it." in page

    chosen = tmp_path / "Study A" / "IDI-07 final.docx"
    chosen.parent.mkdir()
    client.post(reverse("desktop_save", args=[job.public_id, "docx"]), {"target_path": str(chosen)})
    assert chosen.exists() and store.export_dir() == chosen.parent
    page = client.get(job.get_absolute_url()).content.decode()
    assert "IDI-07 final.docx" in page and str(chosen.parent) in page
    # nonsense paths are refused
    client.post(reverse("desktop_save", args=[job.public_id, "docx"]), {"target_path": "relative/x.docx"})
    client.post(reverse("desktop_save", args=[job.public_id, "docx"]), {"target_path": str(tmp_path / "nope" / "x.docx")})
    assert not (tmp_path / "nope").exists()


def test_finished_job_notifies_and_asks_for_manual_check(client, with_key, tone_file, fake_gemini, sync_runner,
                                                         django_capture_on_commit_callbacks):
    from desktop.models import Notification
    from tests.test_app import upload_payload

    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("upload"), upload_payload(tone_file))
    note = Notification.objects.get(kind="done")
    assert "IDI-01 Garment worker" in note.title and "check it against the audio" in note.body
    data = client.get(reverse("desktop_jobs")).json()
    assert data["unread"] == 1 and data["latest"]["title"] == note.title

    page = client.get(reverse("dashboard")).content.decode()
    assert 'id="bell"' in page and "Beta" in page and "Report a problem" in page and "mailto:redoanhossain630@gmail.com" in page
    panel = client.get(reverse("desktop_notifications")).content.decode()
    assert note.title in panel and "Email the developer" in panel
    client.post(reverse("desktop_notifications_read"))
    assert client.get(reverse("desktop_jobs")).json()["unread"] == 0


def test_app_start_welcomes_once_and_reports_cut_off_jobs(with_key):
    from desktop.middleware import local_user
    from desktop.models import Notification
    from desktop.notify import on_app_start

    Job.objects.create(user=local_user(), title="IDI-09", status="processing", progress=40,
                       duration_seconds=600, consent_confirmed=True)
    on_app_start()
    on_app_start()  # opening the app twice does not repeat anything
    assert Notification.objects.filter(dedupe_key="welcome-beta").count() == 1
    stopped = Notification.objects.filter(kind="failed")
    assert stopped.count() == 1 and "IDI-09" in stopped[0].title and "Continue" in stopped[0].body


def test_update_found_becomes_a_notification(client, with_key, settings, monkeypatch):
    from desktop.models import Notification

    settings.GITHUB_REPO, settings.APP_VERSION = "someone/QualResearcher", "1.0.1"
    dviews._update_cache.clear()

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(dviews.urllib.request, "urlopen", lambda req, timeout: Resp(
        b'{"tag_name": "v1.0.2", "html_url": "https://github.com/someone/QualResearcher/releases/tag/v1.0.2"}'))
    client.get(reverse("desktop_update"))
    dviews._update_cache.clear()
    n = Notification.objects.get(kind="update")
    assert n.title == "Version 1.0.2 is available" and n.url.endswith("v1.0.2")


def test_no_notifications_outside_the_desktop_app(settings):
    from desktop.models import Notification
    from desktop.notify import notify

    settings.DESKTOP = False
    notify("info", "x")
    assert not Notification.objects.exists()
