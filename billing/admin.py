# billing/admin.py
from django.contrib import admin

from .models import RazorpayPlan, Subscription


@admin.register(RazorpayPlan)
class RazorpayPlanAdmin(admin.ModelAdmin):
    """Config, not a log — add/edit allowed. Enter the real plan_id from
    Razorpay's dashboard after creating the matching plan there."""
    list_display = ("tier", "interval", "razorpay_plan_id", "amount_paise")
    list_filter = ("tier", "interval")


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    """Read-only — this is a record of what the webhook has reported, not a
    place to hand-edit subscription state."""
    list_display = ("user", "plan", "status", "current_period_end", "created_at")
    list_filter = ("status", "plan")
    search_fields = ("user__email", "razorpay_subscription_id")
    readonly_fields = ("user", "plan", "razorpay_subscription_id", "status", "current_period_end", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
