# billing/services/cross_provider.py
"""
Razorpay and Play Billing are two self-contained, separately-modeled
subscription systems (see PlayBillingSubscription's docstring for why they
aren't a shared base class). A few call sites need to know "does this user
have ANY live paid mandate, regardless of which provider" without caring
which one -- this module is the one place that looks at both tables, so
that knowledge doesn't get silently duplicated (and drift) across
create_user_subscription, process_subscription_grace_periods, and
files/views.py's quota endpoint.
"""
from dataclasses import dataclass

from ..models import PlayBillingSubscription, Subscription


@dataclass
class LiveSubscriptionInfo:
    provider: str  # "razorpay" | "play"
    status: str
    tier: str
    subscription: object  # the actual Subscription or PlayBillingSubscription row


def get_live_subscription(user):
    """
    Returns a LiveSubscriptionInfo for whichever provider currently has an
    in-flight mandate for this user (checking each provider's own
    LIVE_STATUSES), or None if neither does. Razorpay is checked first --
    an arbitrary but stable tie-break, a user should never realistically
    have live mandates on both at once except in the brief window right
    after a Play purchase when a stale Razorpay row hasn't been cleaned up,
    which isn't this function's job to resolve.
    """
    razorpay_sub = (
        Subscription.objects
        .filter(user=user, status__in=Subscription.LIVE_STATUSES)
        .select_related("plan")
        .order_by("-created_at")
        .first()
    )
    if razorpay_sub:
        return LiveSubscriptionInfo(
            provider="razorpay",
            status=razorpay_sub.status,
            tier=razorpay_sub.plan.tier,
            subscription=razorpay_sub,
        )

    play_sub = (
        PlayBillingSubscription.objects
        .filter(user=user, status__in=PlayBillingSubscription.LIVE_STATUSES)
        .select_related("plan")
        .order_by("-created_at")
        .first()
    )
    if play_sub:
        return LiveSubscriptionInfo(
            provider="play",
            status=play_sub.status,
            tier=play_sub.plan.tier,
            subscription=play_sub,
        )

    return None


def has_active_subscription(user) -> bool:
    """
    True if EITHER provider currently entitles this user to a paid tier.
    Deliberately narrower than "has a live mandate" (get_live_subscription) --
    a Razorpay subscription that's merely `created` (mandate started, never
    authenticated) or a Play subscription still `SUBSCRIPTION_STATE_PENDING`
    doesn't actually entitle anyone to anything yet, so this checks the
    smaller "genuinely grants access right now" set used by
    process_subscription_grace_periods to decide whether a downgrade/purge
    should be skipped because the user has since resubscribed (via either
    provider) since their grace/purge timer was set.
    """
    if Subscription.objects.filter(user=user, status="active").exists():
        return True
    return PlayBillingSubscription.objects.filter(
        user=user,
        status__in=("SUBSCRIPTION_STATE_ACTIVE", "SUBSCRIPTION_STATE_IN_GRACE_PERIOD"),
    ).exists()


def pending_grace_or_purge(user):
    """
    Returns whichever provider's row (Razorpay `cancelled`, or Play in a
    grace/purge window) currently has grace_ends_at/purge_at set for this
    user, or None. Used by files/views.py's quota endpoint to surface the
    countdown regardless of which provider the user last subscribed through
    -- previously hardcoded to only look at the Razorpay table.
    """
    from django.db.models import Q

    razorpay_pending = (
        Subscription.objects
        .filter(user=user, status="cancelled")
        .filter(Q(grace_ends_at__isnull=False) | Q(purge_at__isnull=False))
        .order_by("-created_at")
        .first()
    )
    if razorpay_pending:
        return razorpay_pending

    play_pending = (
        PlayBillingSubscription.objects
        .filter(user=user)
        .filter(Q(grace_ends_at__isnull=False) | Q(purge_at__isnull=False))
        .order_by("-created_at")
        .first()
    )
    return play_pending
