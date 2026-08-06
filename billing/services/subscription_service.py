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


class AlreadySubscribed(Exception):
    """Raised instead of creating a second live Razorpay mandate for a user
    who already has one authenticated/active/paused. `subscription` is the
    existing live row, so callers can show which plan/status blocked it."""
    def __init__(self, subscription: Subscription):
        self.subscription = subscription
        super().__init__(
            f"User {subscription.user_id} already has a {subscription.status} "
            f"subscription ({subscription.razorpay_subscription_id})"
        )


def create_user_subscription(user, tier: str, interval: str) -> Subscription:
    """Raises PlanNotConfigured, AlreadySubscribed, or requests.RequestException
    (from create_subscription) -- callers decide how to surface each.

    Guards against the checkout page being reloaded or double-tapped, which
    would otherwise call Razorpay's create-subscription API again and mint a
    second real recurring mandate for the same user:
      - Already authenticated/active/paused (a real mandate exists, possibly
        already collecting) -> reject.
      - Already "created" for this exact plan (mandate started but the
        customer never finished authenticating it) -> reuse that same
        subscription_id instead of minting a second one. This is the
        common case: page reload or double-tap before checkout completed.
      - Otherwise -> create, as before.
    """
    try:
        plan = RazorpayPlan.objects.get(tier=tier, interval=interval)
    except RazorpayPlan.DoesNotExist:
        raise PlanNotConfigured(f"No RazorpayPlan configured for {tier}/{interval}")

    live = (
        Subscription.objects
        .filter(user=user, status__in=Subscription.LIVE_STATUSES)
        .order_by("-created_at")
        .first()
    )
    if live:
        if live.plan_id == plan.id and live.status == "created":
            return live
        raise AlreadySubscribed(live)

    rzp_subscription = create_subscription(plan.razorpay_plan_id)

    return Subscription.objects.create(
        user=user,
        plan=plan,
        razorpay_subscription_id=rzp_subscription["id"],
        status=rzp_subscription.get("status", "created"),
    )
