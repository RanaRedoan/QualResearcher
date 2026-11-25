"""Credit accounting: balance, reserving minutes for a job, refunding a failed job."""
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import CreditGrant, CreditUse


class InsufficientCredits(Exception):
    def __init__(self, needed, available):
        self.needed, self.available = needed, available
        super().__init__(f"Needs {needed} min, only {available} min available")


def active_grants(user):
    return CreditGrant.objects.filter(user=user, remaining__gt=0, expires_at__gt=timezone.now())


def balance_minutes(user):
    return active_grants(user).aggregate(total=Sum("remaining"))["total"] or 0


@transaction.atomic
def reserve_minutes(user, minutes, job):
    """Take `minutes` from the user's grants (soonest-expiring first) for `job`.

    Raises InsufficientCredits if the balance is too small; nothing is taken then.
    """
    grants = list(active_grants(user).select_for_update().order_by("expires_at", "id"))
    available = sum(g.remaining for g in grants)
    if available < minutes:
        raise InsufficientCredits(minutes, available)
    left = minutes
    for grant in grants:
        if left == 0:
            break
        take = min(grant.remaining, left)
        grant.remaining -= take
        grant.save(update_fields=["remaining"])
        CreditUse.objects.create(grant=grant, job=job, minutes=take)
        left -= take
    return minutes


@transaction.atomic
def refund_job(job):
    """Give back every minute charged to `job`. Safe to call more than once."""
    refunded = 0
    uses = CreditUse.objects.select_for_update().filter(job=job, refunded=False).select_related("grant")
    for use in uses:
        grant = use.grant
        if grant.is_expired:
            # The original pack has expired; issue a short-lived refund grant instead.
            CreditGrant.objects.create(
                user=grant.user,
                minutes=use.minutes,
                remaining=use.minutes,
                source=CreditGrant.Source.REFUND,
                expires_at=timezone.now() + timezone.timedelta(days=7),
                note=f"Refund for job #{job.pk}",
            )
        else:
            grant.remaining += use.minutes
            grant.save(update_fields=["remaining"])
        use.refunded = True
        use.save(update_fields=["refunded"])
        refunded += use.minutes
    return refunded
