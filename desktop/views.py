"""Screens and actions that only exist in the installed desktop app."""
import json
import logging
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from transcription import runner
from transcription.models import Job
from transcription.views import export_file

from . import store

log = logging.getLogger(__name__)


def _desktop_only(view):
    def wrapper(request, *args, **kwargs):
        if not settings.DESKTOP:
            raise Http404
        return view(request, *args, **kwargs)
    wrapper.__name__ = view.__name__
    return wrapper


# ---------------------------------------------------------------- Gemini key
def check_key(key: str) -> tuple[bool, str]:
    """Ask Gemini for one word with this key. Returns (works, message for the user)."""
    key = (key or "").strip()
    if not key:
        return False, "Paste your Gemini API key first."
    if len(key) < 20 or any(c.isspace() for c in key):
        return False, "That does not look like a whole Gemini API key. Copy it again from AI Studio with the Copy button."
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=key)
        client.models.generate_content(
            model=settings.GEMINI_TRANSCRIBE_MODEL, contents="Reply with the single word OK.",
            config=types.GenerateContentConfig(max_output_tokens=20),
        )
        return True, "The key works."
    except Exception as exc:  # the SDK raises different classes for each failure
        text = str(exc)
        code = getattr(exc, "code", None)
        if "API_KEY_INVALID" in text or "API key not valid" in text or code == 400 and "key" in text.lower():
            return False, "Google says this key is not valid. Copy it again from AI Studio, the whole key."
        if code == 403 or "PERMISSION_DENIED" in text:
            return False, "This key is not allowed to use Gemini. Create a new key in AI Studio."
        if code == 429 or "RESOURCE_EXHAUSTED" in text:
            return True, "The key works, but its limit is used up for now. It will work again later."
        if code in (500, 502, 503, 504):
            return True, "The key looks fine. Google is busy right now, which is normal now and then."
        log.warning("Key test failed: %s", text[:300])
        return False, "Could not reach Google. Check the internet connection and try again."


def _usable(ok, msg):
    """Save the key when it works, or when Google simply could not be reached to check it."""
    return ok or msg.startswith("Could not reach")


@_desktop_only
def setup(request):
    """First screen after installing: name + the first Gemini key."""
    cfg = store.load()
    error = None
    if request.method == "POST":
        key = request.POST.get("gemini_api_key", "").strip()
        ok, msg = check_key(key)
        if _usable(ok, msg):
            store.save(name=request.POST.get("name", "").strip()[:150])
            store.add_key(request.POST.get("key_name", "") or "My key", key, make_default=True)
            if ok:
                messages.success(request, "All set. Drop a recording to start your first transcript.")
            else:
                messages.warning(request, "Saved, but the key could not be tested because Google could not be reached.")
            return redirect("upload")
        error = msg
    return render(request, "desktop/setup.html", {"cfg": cfg, "error": error,
                                                  "posted_key": request.POST.get("gemini_api_key", ""),
                                                  "posted_name": request.POST.get("key_name", "")})


@_desktop_only
@require_POST
def test_key(request):
    """Test a pasted key, or a saved one (key_id)."""
    key = request.POST.get("gemini_api_key", "").strip()
    if not key and request.POST.get("key_id"):
        entry = store.get_key(request.POST["key_id"])
        key = entry["key"] if entry and entry["id"] == request.POST["key_id"] else ""
    ok, msg = check_key(key)
    return JsonResponse({"ok": ok, "message": msg})


@_desktop_only
@login_required
def app_settings(request):
    cfg = store.load()
    if request.method == "POST":
        export_dir = request.POST.get("export_dir", "").strip()
        store.save(
            name=request.POST.get("name", "").strip()[:150],
            english_words=request.POST.get("english_words", "bangla"),
            export_dir="" if not export_dir or export_dir == str(settings.DEFAULT_EXPORT_DIR) else export_dir,
        )
        messages.success(request, "Settings saved.")
        return redirect("desktop_settings")
    keys = [dict(k, masked=store.mask(k["key"]), default=k["id"] == cfg["default_key"]) for k in cfg["keys"]]
    return render(request, "desktop/settings.html", {
        "cfg": cfg, "keys": keys,
        "default_export": settings.DEFAULT_EXPORT_DIR, "data_dir": settings.DATA_DIR,
    })


@_desktop_only
@login_required
@require_POST
def key_add(request):
    key = request.POST.get("gemini_api_key", "").strip()
    name = request.POST.get("key_name", "").strip()
    ok, msg = check_key(key)
    if not _usable(ok, msg):
        messages.error(request, f"Key not added. {msg}")
    else:
        entry = store.add_key(name, key, make_default=request.POST.get("make_default") == "on")
        messages.success(request, f"Key “{entry['name']}” added." + ("" if ok else " It could not be tested right now."))
    return redirect("desktop_settings")


@_desktop_only
@login_required
@require_POST
def key_action(request, key_id):
    action = request.POST.get("action")
    entry = store.get_key(key_id)
    if not entry or entry["id"] != key_id:
        return redirect("desktop_settings")
    if action == "default":
        store.save(default_key=key_id)
        messages.success(request, f"“{entry['name']}” is now the default key.")
    elif action == "rename":
        store.rename_key(key_id, request.POST.get("key_name", ""))
    elif action == "delete":
        if len(store.keys()) == 1:
            messages.error(request, "This is your only key. Add another key before deleting it.")
        else:
            store.remove_key(key_id)
            messages.success(request, f"Key “{entry['name']}” deleted from this computer.")
    return redirect("desktop_settings")


# ---------------------------------------------------------------- files
def _open(path: Path):
    """Open a file or folder with Windows (Word for .docx, Explorer for folders)."""
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # noqa: S606 — local desktop app opening its own output
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError as exc:
        log.warning("Could not open %s: %s", path, exc)


def _reveal(path: Path):
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", str(path)])
    else:
        _open(path.parent)


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    for n in range(2, 1000):
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not candidate.exists():
            return candidate
    return path


@_desktop_only
@login_required
@require_POST
def save_word(request, public_id, kind):
    """Write the Word file into the export folder, then open it in Word."""
    job = get_object_or_404(Job, public_id=public_id, user=request.user)
    result = export_file(job, kind)
    if result is None:
        messages.error(request, "This transcript is not ready yet.")
        return redirect(job)
    buf, filename = result
    chosen = request.POST.get("target_path", "").strip()
    try:
        if chosen:  # picked in the Windows "Save As" box, which already asked about replacing a file
            target = Path(chosen)
            if not target.is_absolute() or target.suffix.lower() != ".docx" or not target.parent.is_dir():
                messages.error(request, "That place can't be used. Choose a folder that exists and a name ending in .docx.")
                return redirect(job)
            if target.parent != store.export_dir():
                store.save(export_dir=str(target.parent))  # next time the box opens in this folder
        else:
            folder = store.export_dir()
            folder.mkdir(parents=True, exist_ok=True)
            target = _unique(folder / filename)
        target.write_bytes(buf.getvalue())
    except OSError as exc:
        messages.error(request, f"Could not save the Word file: {exc}. Is it open in Word? Close it, or choose another name.")
        return redirect(job)
    request.session["last_export"] = {"job": str(job.public_id), "path": str(target), "name": target.name,
                                      "folder": str(target.parent)}
    if request.POST.get("open", "1") == "1":
        _open(target)
    return redirect(job)


@_desktop_only
@login_required
@require_POST
def reveal(request):
    """Show a saved file (or a known folder) in Windows Explorer."""
    what = request.POST.get("what", "")
    if what == "exports":
        folder = store.export_dir()
        folder.mkdir(parents=True, exist_ok=True)
        _open(folder)
    elif what == "data":
        _open(Path(settings.DATA_DIR))
    else:
        last = request.session.get("last_export") or {}
        path = Path(last.get("path", ""))
        if last and path.exists():
            if what == "open":
                _open(path)
            else:
                _reveal(path)
    nxt = request.POST.get("next", "")
    return redirect(nxt if url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}) else "dashboard")


# ---------------------------------------------------------------- small JSON helpers
@_desktop_only
@login_required
def jobs_json(request):
    """Polled by the job list so status and progress stay current without reloading."""
    out = []
    for j in request.user.jobs.defer("segments", "partial", "live_segments")[:300]:
        stalled = (not j.is_finished) and not runner.is_alive_here(j.pk)
        out.append({"id": str(j.public_id), "status": "stopped" if stalled else j.status,
                    "label": "Stopped" if stalled else j.get_status_display(), "progress": j.progress})
    from .models import Notification

    latest = Notification.objects.first()
    return JsonResponse({
        "jobs": out, "running": len(runner.RUNNING | runner.QUEUED | runner.DELAYED),
        "unread": Notification.objects.filter(read_at__isnull=True).count(),
        "latest": {"id": latest.id, "kind": latest.kind, "title": latest.title, "body": latest.body,
                   "url": latest.url} if latest else None,
    })


@_desktop_only
@login_required
def notifications(request):
    """The list shown when the bell is clicked (an HTML fragment)."""
    from .models import Notification

    items = list(Notification.objects.all()[:40])
    return render(request, "desktop/_notifications.html", {"items": items})


@_desktop_only
@login_required
@require_POST
def notifications_read(request):
    from django.utils import timezone

    from .models import Notification

    Notification.objects.filter(read_at__isnull=True).update(read_at=timezone.now())
    return JsonResponse({"ok": True})


_update_cache: dict = {}


def _version_tuple(v):
    try:
        return tuple(int(x) for x in v.lstrip("vV").split(".")[:3])
    except ValueError:
        return (0,)


@_desktop_only
def update_check(request):
    """Is a newer installer on the GitHub Releases page? Checked once per app start."""
    if not settings.GITHUB_REPO:
        return JsonResponse({"update": False})
    if "result" not in _update_cache:
        result = {"update": False}
        try:
            req = urllib.request.Request(
                f"https://api.github.com/repos/{settings.GITHUB_REPO}/releases/latest",
                headers={"Accept": "application/vnd.github+json", "User-Agent": "QualResearcher"})
            with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 — fixed https URL
                data = json.loads(resp.read().decode("utf-8"))
            latest = data.get("tag_name", "")
            if _version_tuple(latest) > _version_tuple(settings.APP_VERSION):
                result = {"update": True, "version": latest.lstrip("vV"), "url": data.get("html_url", "")}
                from .notify import update_available

                update_available(result["version"], result["url"])
        except Exception as exc:  # offline, rate-limited … just say nothing
            log.info("Update check skipped: %s", exc)
        _update_cache["result"] = result
    return JsonResponse(_update_cache["result"])


@_desktop_only
def help_gemini_key(request):
    """Step-by-step guide to getting a Gemini key; open even before a key is saved."""
    layout = "layouts/app.html" if store.api_key() else "layouts/bare.html"
    return render(request, "desktop/help_gemini_key.html", {"layout": layout})


def ping(request):
    return JsonResponse({"ok": True, "version": settings.APP_VERSION})
