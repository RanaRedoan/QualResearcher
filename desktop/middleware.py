"""Desktop app only: there is one person per PC, so nobody logs in, and the app asks
for a Gemini key before anything else."""
from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.shortcuts import redirect
from django.urls import reverse

from . import store

LOCAL_EMAIL = "me@qualresearcher.local"


def local_user():
    User = get_user_model()
    user, created = User.objects.get_or_create(
        email=LOCAL_EMAIL, defaults={"is_approved": True, "is_staff": True, "is_superuser": True})
    if created:
        user.set_unusable_password()
        user.save(update_fields=["password"])
    name = store.load()["name"]
    if name and user.full_name != name:
        user.full_name = name
        user.save(update_fields=["full_name"])
    return user


class DesktopMiddleware:
    OPEN_PREFIXES = ("/static/", "/desktop/setup/", "/desktop/test-key/", "/desktop/ping/", "/desktop/help/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not settings.DESKTOP:
            return self.get_response(request)
        if not request.user.is_authenticated:
            login(request, local_user(), backend="django.contrib.auth.backends.ModelBackend")
        path = request.path
        if not store.api_key() and not path.startswith(self.OPEN_PREFIXES):
            return redirect(reverse("desktop_setup"))
        # pages that make no sense in the app (sign-up, pricing, password reset …)
        if path.startswith(("/accounts/", "/billing/", "/pricing/", "/terms/")) or path == "/":
            return redirect(reverse("dashboard"))
        return self.get_response(request)
