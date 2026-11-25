from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.utils import timezone


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra):
        if not email:
            raise ValueError("Email is required")
        email = self.normalize_email(email).lower()
        user = self.model(email=email, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("is_approved", True)
        return self._create_user(email, password, **extra)


class User(AbstractUser):
    """Researcher account. Logs in with email; must be approved before uploading."""

    username = None
    email = models.EmailField("email address", unique=True)
    full_name = models.CharField(max_length=150, blank=True)
    organization = models.CharField(max_length=200, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    is_approved = models.BooleanField(
        default=False, help_text="Approved accounts can upload audio."
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    def __str__(self):
        return self.email

    @property
    def display_name(self):
        return self.full_name or self.email.split("@")[0]

    def approve(self, trial_minutes=0, trial_days=14):
        """Approve the account and optionally give free trial minutes."""
        from billing.models import CreditGrant

        if self.is_approved:
            return False
        self.is_approved = True
        self.approved_at = timezone.now()
        self.save(update_fields=["is_approved", "approved_at"])
        if trial_minutes > 0:
            CreditGrant.objects.create(
                user=self,
                minutes=trial_minutes,
                remaining=trial_minutes,
                source=CreditGrant.Source.TRIAL,
                expires_at=timezone.now() + timezone.timedelta(days=trial_days),
                note="Free trial",
            )
        return True
