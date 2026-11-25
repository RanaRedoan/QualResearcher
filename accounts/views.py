from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core.mail import mail_admins, send_mail
from django.shortcuts import redirect, render

from .forms import ProfileForm, SignupForm
from .models import User


def signup(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        first_local_user = settings.LOCAL_MODE and not User.objects.exists()
        user = form.save()
        if first_local_user:  # the PC owner becomes admin
            user.is_staff = user.is_superuser = True
            user.save(update_fields=["is_staff", "is_superuser"])
        if not settings.REQUIRE_APPROVAL:
            user.approve(trial_minutes=settings.TRIAL_MINUTES if settings.BILLING_ENABLED else 0,
                         trial_days=settings.TRIAL_VALID_DAYS)
        _notify_admin_of_signup(user)
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        messages.success(request, "Welcome! Your account has been created.")
        return redirect("dashboard")
    return render(request, "accounts/signup.html", {"form": form})


def _notify_admin_of_signup(user):
    if not settings.ADMIN_NOTIFY_EMAIL:
        return
    try:
        send_mail(
            f"[{settings.SITE_NAME}] New signup: {user.email}",
            f"{user.full_name} ({user.organization or 'no organization'}) signed up.\n"
            f"Phone: {user.phone or '-'}\nApprove them in the admin panel.",
            settings.DEFAULT_FROM_EMAIL,
            [settings.ADMIN_NOTIFY_EMAIL],
            fail_silently=True,
        )
    except Exception:  # never block signup on email problems
        pass


@login_required
def profile(request):
    form = ProfileForm(request.POST or None, instance=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Profile updated.")
        return redirect("profile")
    return render(request, "accounts/profile.html", {"form": form})
