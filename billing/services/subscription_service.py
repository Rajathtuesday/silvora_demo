# billing/services/subscription_service.py
"""
Shared by the web checkout page -- subscribing happens entirely on
silvora.cloud now, not inside the app, specifically to avoid Google Play's
requirement that in-app digital subscriptions go through Play Billing.
The app only ever asks for a signed link to here; this is where the actual
Razorpay subscription gets created.
"""
from ..models import RazorpayPlan, Subscription
from .razorpay_client import create_subscription


class PlanNotConfigured(Exception):
    pass


def create_user_subscription(user, tier: str, interval: str) -> Subscription:
    """Raises PlanNotConfigured or requests.RequestException (from
    create_subscription) -- callers decide how to surface each."""
    try:
        plan = RazorpayPlan.objects.get(tier=tier, interval=interval)
    except RazorpayPlan.DoesNotExist:
        raise PlanNotConfigured(f"No RazorpayPlan configured for {tier}/{interval}")

    rzp_subscription = create_subscription(plan.razorpay_plan_id)

    return Subscription.objects.create(
        user=user,
        plan=plan,
        razorpay_subscription_id=rzp_subscription["id"],
        status=rzp_subscription.get("status", "created"),
    )
