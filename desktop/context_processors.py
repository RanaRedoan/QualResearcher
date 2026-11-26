from urllib.parse import quote

from django.conf import settings

from . import store


def desktop(request):
    """Values every page needs. Outside the desktop app only the layout name is set."""
    if not settings.DESKTOP:
        return {"DESKTOP": False, "BASE_LAYOUT": "layouts/web.html"}
    from transcription import runner

    ctx = {
        "DESKTOP": True,
        "BASE_LAYOUT": "layouts/app.html",
        "APP_VERSION": settings.APP_VERSION,
        "KEY_SET": bool(store.api_key()),
        "DEFAULT_KEY_NAME": store.key_name(),
        "RUNNING_COUNT": len(runner.RUNNING | runner.QUEUED | runner.DELAYED),
        "EXPORT_DIR": str(store.export_dir()),
        "GITHUB_REPO": settings.GITHUB_REPO,
        "SIDEBAR_JOBS": [],
        "UNREAD_COUNT": 0,
        "REPORT_MAILTO": (f"mailto:{settings.DEVELOPER_EMAIL}?subject="
                          + quote(f"{settings.SITE_NAME} {settings.APP_VERSION} beta: problem report")
                          + "&body=" + quote("What happened:\n\nWhat I expected:\n\nSteps (which file, which button):\n\n"
                                             f"App version: {settings.APP_VERSION}\n"
                                             "If you can, attach the log file: %LOCALAPPDATA%\\QualResearcher\\logs\\app.log")),
    }
    try:
        from .models import Notification

        ctx["UNREAD_COUNT"] = Notification.objects.filter(read_at__isnull=True).count()
        latest = Notification.objects.values_list("id", flat=True).first()
        ctx["LATEST_NOTIFICATION_ID"] = latest or 0
    except Exception:  # table not created yet (first start before migrate)
        pass
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        # the big transcript columns are not needed for the list
        jobs = list(user.jobs.defer("segments", "partial", "live_segments")[:300])
        for j in jobs:
            j.stalled = (not j.is_finished) and not runner.is_alive_here(j.pk)
        ctx["SIDEBAR_JOBS"] = jobs
        match = getattr(request, "resolver_match", None)
        ctx["CURRENT_JOB"] = str(match.kwargs.get("public_id", "")) if match else ""
    return ctx
