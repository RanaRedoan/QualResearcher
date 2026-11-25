from django.conf import settings
from django.shortcuts import redirect, render

from billing.models import Plan


def home(request):
    if settings.LOCAL_MODE:  # on a personal PC there is no marketing page — go straight to work
        return redirect("dashboard")
    plans = Plan.objects.filter(is_active=True)
    lowest = min((p.rate_per_hour for p in plans), default=None)
    return render(request, "pages/home.html", {"plans": plans, "lowest_rate": lowest})


def pricing(request):
    return render(request, "pages/pricing.html", {"plans": Plan.objects.filter(is_active=True)})


def privacy(request):
    return render(request, "pages/privacy.html")


def terms(request):
    return render(request, "pages/terms.html")
