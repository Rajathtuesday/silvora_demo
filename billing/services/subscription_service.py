# billing/services/subscription_service.py
"""
Shared by the web checkout page -- subscribing happens entirely on
silvora.cloud now, not inside the app, specifically to avoid Google Play's
requirement that in-app digital subscriptions go through Play Billing.
The app only ever asks for a signed link to here; this is where the actual
Razorpay subscription gets created.
"""
from ..models import RazorpayPlan, Subscription
from .cross_provider import get_live_subscription
from .razorpay_client import create_subscription


class PlanNotConfigured(Exception):
    pass


class AlreadySubscribed(Exception):
    """Raised instead of creating a second live mandate for a user who
    already has one, on EITHER provider (`subscription` may be a Razorpay
    `Subscription` or a `PlayBillingSubscription` row -- see
    cross_provider.py). `subscription` is the existing live row, so callers
    can show which plan/status blocked it."""
    def __init__(self, subscription):
        self.subscription = subscription
        identifier = getattr(subscription, "razorpay_subscription_id", None) or getattr(
            subscription, "purchase_token", None
        )
        super().__init__(
            f"User {subscription.user_id} already has a {subscription.status} "
            f"subscription ({identifier})"
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

    # A live Play Billing mandate blocks a new Razorpay one the same way an
    # existing live Razorpay row does above -- without this, a user with an
    # active Play subscription could open the website checkout and mint a
    # second, real, paying mandate through the other provider.
    live_elsewhere = get_live_subscription(user)
    if live_elsewhere and live_elsewhere.provider == "play":
        raise AlreadySubscribed(live_elsewhere.subscription)

    rzp_subscription = create_subscription(plan.razorpay_plan_id)

    return Subscription.objects.create(
        user=user,
        plan=plan,
        razorpay_subscription_id=rzp_subscription["id"],
        status=rzp_subscription.get("status", "created"),
    )
