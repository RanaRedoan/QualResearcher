from django.conf import settings
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ("-date_joined",)
    list_display = ("email", "full_name", "organization", "phone", "is_approved", "credit_balance", "date_joined")
    list_filter = ("is_approved", "is_staff", "is_active")
    search_fields = ("email", "full_name", "organization", "phone")
    actions = ["approve_with_trial", "approve_without_trial"]
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Profile", {"fields": ("full_name", "organization", "phone")}),
        ("Access", {"fields": ("is_approved", "approved_at", "is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Dates", {"fields": ("last_login", "date_joined")}),
    )
    readonly_fields = ("approved_at", "last_login", "date_joined")
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("email", "password1", "password2", "is_approved")}),)

    @admin.display(description="Minutes left")
    def credit_balance(self, obj):
        from billing.services import balance_minutes

        return balance_minutes(obj)

    @admin.action(description=f"Approve and give trial minutes ({settings.TRIAL_MINUTES} min)")
    def approve_with_trial(self, request, queryset):
        n = sum(u.approve(settings.TRIAL_MINUTES, settings.TRIAL_VALID_DAYS) for u in queryset)
        self.message_user(request, f"Approved {n} account(s) with trial minutes.", messages.SUCCESS)

    @admin.action(description="Approve without trial minutes")
    def approve_without_trial(self, request, queryset):
        n = sum(u.approve(0) for u in queryset)
        self.message_user(request, f"Approved {n} account(s).", messages.SUCCESS)
