from django.conf import settings
from django.db import models, transaction
from django.utils import timezone


class Plan(models.Model):
    """A prepaid credit pack shown on the pricing page."""

    name = models.CharField(max_length=60)
    minutes = models.PositiveIntegerField()
    price_bdt = models.PositiveIntegerField("Price (BDT)")
    validity_days = models.PositiveIntegerField(default=30)
    highlight = models.BooleanField(default=False, help_text="Show as 'Most popular'")
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("sort_order", "price_bdt")

    def __str__(self):
        return f"{self.name} — {self.hours:g} h — ৳{self.price_bdt:,}"

    @property
    def hours(self):
        return self.minutes / 60

    @property
    def rate_per_hour(self):
        return round(self.price_bdt / self.hours) if self.minutes else 0


class CreditGrant(models.Model):
    """A batch of minutes a user can spend. Spent oldest-expiring first."""

    class Source(models.TextChoices):
        PURCHASE = "purchase", "Purchase"
        TRIAL = "trial", "Free trial"
        MANUAL = "manual", "Manual (admin)"
        REFUND = "refund", "Refund"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="credit_grants")
    minutes = models.PositiveIntegerField()
    remaining = models.PositiveIntegerField()
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.MANUAL)
    expires_at = models.DateTimeField()
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("expires_at",)

    def __str__(self):
        return f"{self.user} {self.remaining}/{self.minutes} min (until {self.expires_at:%Y-%m-%d})"

    @property
    def is_expired(self):
        return self.expires_at <= timezone.now()


class CreditUse(models.Model):
    """Minutes taken from a grant for one transcription job (kept for refunds/audit)."""

    grant = models.ForeignKey(CreditGrant, on_delete=models.CASCADE, related_name="uses")
    job = models.ForeignKey("transcription.Job", on_delete=models.SET_NULL, null=True, related_name="credit_uses")
    minutes = models.PositiveIntegerField()
    refunded = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)


class PaymentRequest(models.Model):
    """A bKash 'Send Money' payment the user reports with its Transaction ID."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="payments")
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT)
    amount_bdt = models.PositiveIntegerField()
    method = models.CharField(max_length=20, default="bkash")
    sender_number = models.CharField(max_length=20)
    trx_id = models.CharField("Transaction ID", max_length=40, unique=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    admin_note = models.CharField(max_length=255, blank=True)
    grant = models.OneToOneField(CreditGrant, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"{self.trx_id} — {self.user} — ৳{self.amount_bdt}"

    @transaction.atomic
    def approve(self, note=""):
        if self.status == self.Status.APPROVED:
            return False
        self.grant = CreditGrant.objects.create(
            user=self.user,
            minutes=self.plan.minutes,
            remaining=self.plan.minutes,
            source=CreditGrant.Source.PURCHASE,
            expires_at=timezone.now() + timezone.timedelta(days=self.plan.validity_days),
            note=f"{self.plan.name} (bKash {self.trx_id})",
        )
        self.status = self.Status.APPROVED
        self.admin_note = note
        self.reviewed_at = timezone.now()
        self.save()
        return True

    def reject(self, note=""):
        if self.status != self.Status.PENDING:
            return False
        self.status = self.Status.REJECTED
        self.admin_note = note
        self.reviewed_at = timezone.now()
        self.save()
        return True
