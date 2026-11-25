from django.contrib import admin, messages

from .models import CreditGrant, CreditUse, PaymentRequest, Plan


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("name", "minutes", "price_bdt", "rate_per_hour", "validity_days", "highlight", "is_active", "sort_order")
    list_editable = ("price_bdt", "highlight", "is_active", "sort_order")


@admin.register(PaymentRequest)
class PaymentRequestAdmin(admin.ModelAdmin):
    list_display = ("trx_id", "user", "plan", "amount_bdt", "sender_number", "status", "created_at")
    list_filter = ("status", "plan")
    search_fields = ("trx_id", "user__email", "sender_number")
    readonly_fields = ("grant", "created_at", "reviewed_at")
    actions = ["approve_payments", "reject_payments"]

    @admin.action(description="Approve: TrxID verified in bKash app → add credits")
    def approve_payments(self, request, queryset):
        n = sum(p.approve() for p in queryset)
        self.message_user(request, f"Approved {n} payment(s); credits added.", messages.SUCCESS)

    @admin.action(description="Reject: TrxID not found / amount wrong")
    def reject_payments(self, request, queryset):
        n = sum(p.reject("Could not verify this transaction.") for p in queryset)
        self.message_user(request, f"Rejected {n} payment(s).", messages.WARNING)


class CreditUseInline(admin.TabularInline):
    model = CreditUse
    extra = 0
    readonly_fields = ("job", "minutes", "refunded", "created_at")
    can_delete = False


@admin.register(CreditGrant)
class CreditGrantAdmin(admin.ModelAdmin):
    list_display = ("user", "source", "minutes", "remaining", "expires_at", "note", "created_at")
    list_filter = ("source",)
    search_fields = ("user__email", "note")
    inlines = [CreditUseInline]
