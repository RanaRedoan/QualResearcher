from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from django.shortcuts import redirect, render

from .forms import PaymentForm
from .models import CreditGrant, Plan
from .services import balance_minutes


def billing_required(view):
    """Credits pages only exist when billing is switched on (public website mode)."""
    def wrapper(request, *args, **kwargs):
        if not settings.BILLING_ENABLED:
            return redirect("dashboard")
        return view(request, *args, **kwargs)
    wrapper.__name__ = view.__name__
    return wrapper


@billing_required
@login_required
def buy_credits(request):
    initial = {}
    if plan_id := request.GET.get("plan"):
        initial["plan"] = Plan.objects.filter(pk=plan_id, is_active=True).first()
    form = PaymentForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        payment = form.save(commit=False)
        payment.user = request.user
        payment.amount_bdt = payment.plan.price_bdt
        payment.save()
        if settings.ADMIN_NOTIFY_EMAIL:
            send_mail(
                f"[{settings.SITE_NAME}] Payment to verify: {payment.trx_id}",
                f"{request.user.email} says they paid ৳{payment.amount_bdt} for {payment.plan.name}\n"
                f"from {payment.sender_number}, TrxID {payment.trx_id}.",
                settings.DEFAULT_FROM_EMAIL,
                [settings.ADMIN_NOTIFY_EMAIL],
                fail_silently=True,
            )
        messages.success(
            request,
            "Thanks! We received your Transaction ID. Credits are added as soon as we verify the payment.",
        )
        return redirect("billing")
    return render(request, "billing/buy.html", {"form": form, "plans": Plan.objects.filter(is_active=True)})


@billing_required
@login_required
def billing_overview(request):
    return render(
        request,
        "billing/overview.html",
        {
            "balance": balance_minutes(request.user),
            "grants": CreditGrant.objects.filter(user=request.user).order_by("-created_at")[:50],
            "payments": request.user.payments.select_related("plan")[:50],
        },
    )
