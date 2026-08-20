# billing/services/play_purchase_service.py
"""
Mirrors subscription_service.py's role for the Razorpay flow: this is where
a verified Google Play purchase actually gets turned into a local
subscription row and a granted tier. Never trusts anything the client
reports (product id, tier, account) -- only the VERIFIED response from
Google's own API decides what gets granted.
"""
import logging

from django.conf import settings
from django.utils import timezone
from googleapiclient.errors import HttpError

from ..models import PlayBillingPlan, PlayBillingSubscription
from .obfuscated_account import make_obfuscated_account_id
from .play_billing_client import acknowledge_subscription_purchase, get_subscription_purchase

logger = logging.getLogger("silvora.billing")

# Google's own subscriptionState values that mean "this purchase currently
# entitles the user to the tier" -- used by sync_subscription_state to decide
# grant vs. no-op vs. downgrade. Deliberately not the same set as
# PlayBillingSubscription.LIVE_STATUSES (which includes CANCELED, since a
# cancelled-but-not-yet-expired mandate is still "in flight" for the
# double-subscription guard, but IS still entitled -- see ENTITLED_STATUSES
# including CANCELED too, for exactly that reason).
ENTITLED_STATUSES = (
    "SUBSCRIPTION_STATE_ACTIVE",
    "SUBSCRIPTION_STATE_IN_GRACE_PERIOD",
    "SUBSCRIPTION_STATE_CANCELED",  # auto-renew off != access off yet
)


class InvalidPurchaseToken(Exception):
    pass


class PlanNotConfigured(Exception):
    pass


def _extract_line_item(verified: dict) -> dict:
    line_items = verified.get("lineItems") or []
    if not line_items:
        raise InvalidPurchaseToken("Verified purchase has no line items")
    return line_items[0]


def sync_subscription_state(subscription: PlayBillingSubscription, verified: dict) -> None:
    """
    Single source of truth turning a verified SubscriptionPurchaseV2 response
    into local status/current_period_end plus side effects (tier grant or
    downgrade, grace timers, throttled emails). Called from BOTH
    verify_and_grant_play_purchase (first purchase) and the RTDN webhook
    (every later event) -- Razorpay only has one entry point for this (its
    webhook); Play needs it reachable from two, so this keeps them from ever
    disagreeing about what a given Google-reported state means.
    """
    from users.models import UserQuota

    new_status = verified.get("subscriptionState", "SUBSCRIPTION_STATE_UNSPECIFIED")
    expiry_time = None
    line_item = verified.get("lineItems") or []
    if line_item:
        expiry_str = line_item[0].get("expiryTime")
        if expiry_str:
            from django.utils.dateparse import parse_datetime
            expiry_time = parse_datetime(expiry_str)

    previous_status = subscription.status
    subscription.status = new_status
    if expiry_time:
        subscription.current_period_end = expiry_time

    quota, _ = UserQuota.objects.get_or_create(user=subscription.user)

    # Entitlement (grant vs. downgrade) and side-effect emails are two
    # separate concerns, checked independently -- IN_GRACE_PERIOD is a
    # member of ENTITLED_STATUSES (still keeps access) AND needs its own
    # throttled email, so it can't be a single if/elif chain keyed on the
    # same membership check without the email branch becoming unreachable.
    if new_status in ENTITLED_STATUSES:
        # Recovered from grace/hold, or a fresh grant -- clear any
        # in-progress downgrade timers and (re)grant the tier.
        subscription.grace_ends_at = None
        subscription.purge_at = None
        quota.set_tier(subscription.plan.tier)

    if new_status == "SUBSCRIPTION_STATE_IN_GRACE_PERIOD":
        # Google's own dunning window -- distinct from Silvora's post-
        # cancellation grace below. Access already kept on by the ENTITLED_STATUSES
        # branch above. Throttled email only.
        now = timezone.now()
        already_notified_recently = (
            subscription.last_payment_issue_notified_at is not None
            and now - subscription.last_payment_issue_notified_at < timezone.timedelta(hours=24)
        )
        if not already_notified_recently and subscription.user.email:
            _send_payment_issue_email(subscription.user)
            subscription.last_payment_issue_notified_at = now

    elif new_status == "SUBSCRIPTION_STATE_ON_HOLD":
        # Google's dunning is exhausted, access already suspended on
        # Google's side -- downgrade immediately. No extra 7-day grace on
        # top (Google already gave that grace via the prior IN_GRACE_PERIOD
        # state) -- purge_at only.
        if previous_status != "SUBSCRIPTION_STATE_ON_HOLD":
            quota.set_tier("free")
            subscription.purge_at = timezone.now() + timezone.timedelta(days=30)

    elif new_status == "SUBSCRIPTION_STATE_EXPIRED":
        # Entitlement genuinely over now -- Play's equivalent of Razorpay's
        # cancelled/completed. Idempotent against redelivery: only start the
        # timers and send the email once.
        if previous_status != "SUBSCRIPTION_STATE_EXPIRED":
            subscription.grace_ends_at = timezone.now() + timezone.timedelta(days=7)
            subscription.purge_at = timezone.now() + timezone.timedelta(days=30)
            if subscription.user.email:
                _send_subscription_ended_email(subscription.user)

    elif new_status == "SUBSCRIPTION_STATE_REVOKED":
        # Refund/chargeback -- a reversal, not a natural lapse. Downgrade
        # immediately, skip the soft 7-day grace.
        if previous_status != "SUBSCRIPTION_STATE_REVOKED":
            quota.set_tier("free")
            subscription.purge_at = timezone.now() + timezone.timedelta(days=30)

    elif new_status == "SUBSCRIPTION_STATE_PAUSED":
        if previous_status != "SUBSCRIPTION_STATE_PAUSED":
            quota.set_tier("free")

    subscription.save()


def _send_payment_issue_email(user):
    from django.core.mail import send_mail
    try:
        send_mail(
            subject="Silvora payment failed",
            message=(
                "Your recent Silvora subscription payment through Google Play didn't "
                "go through. Google will retry automatically -- please make sure your "
                "payment method is up to date to avoid any interruption."
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=True,
        )
    except Exception as e:
        logger.error("Failed to send Play payment-issue notice to user %s: %s", user.id, e)


def _send_subscription_ended_email(user):
    from django.core.mail import send_mail
    try:
        send_mail(
            subject="Your Silvora subscription has ended",
            message=(
                "Your Silvora subscription has ended. You'll keep your current "
                "storage limit for 7 more days -- plenty of time to download "
                "anything over the free 1GB tier, or to resubscribe.\n\n"
                "After 7 days, your account moves to the free 1GB tier (your "
                "files stay put, you just can't add more until you're back "
                "under the limit). Files still over the limit after 30 days "
                "total get permanently deleted.\n\n"
                "Resubscribe any time before then to keep everything exactly "
                "as it is."
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=True,
        )
    except Exception as e:
        logger.error("Failed to send Play cancellation notice to user %s: %s", user.id, e)


def verify_and_grant_play_purchase(user, product_id: str, purchase_token: str) -> PlayBillingSubscription:
    """
    Raises InvalidPurchaseToken, PlanNotConfigured, or HttpError (from
    get_subscription_purchase) -- callers decide how to surface each.

    1. Idempotent replay: an existing row for this exact purchase_token is
       returned as-is, no re-verify, no double grant -- the Flutter client
       may retry this call after a network blip before completePurchase().
    2. Verify against Google's own API -- an unrecognized/wrong-package
       token raises here and gets rejected before anything local changes.
    3. Look up PlayBillingPlan from the VERIFIED response's product/base-plan
       ids, never from the client-submitted product_id argument.
    4. Recompute the expected obfuscated account id and compare against the
       verified response's own externalAccountIdentifiers -- this is what
       stops a purchase token observed under one session from being
       replayed to credit a different account.
    5. Upsert the row and hand off to sync_subscription_state for the actual
       state/tier logic.
    6. Best-effort acknowledge.
    """
    existing = PlayBillingSubscription.objects.filter(purchase_token=purchase_token).first()
    if existing:
        return existing

    verified = get_subscription_purchase(settings.GOOGLE_PLAY_PACKAGE_NAME, purchase_token)

    line_item = _extract_line_item(verified)
    verified_product_id = line_item.get("productId")
    verified_base_plan_id = (line_item.get("offerDetails") or {}).get("basePlanId")

    try:
        plan = PlayBillingPlan.objects.get(
            play_product_id=verified_product_id,
            play_base_plan_id=verified_base_plan_id,
        )
    except PlayBillingPlan.DoesNotExist:
        raise PlanNotConfigured(
            f"No PlayBillingPlan configured for product={verified_product_id} "
            f"base_plan={verified_base_plan_id}"
        )

    expected_account_id = make_obfuscated_account_id(user)
    reported_account_id = (verified.get("externalAccountIdentifiers") or {}).get(
        "obfuscatedExternalAccountId"
    )
    if reported_account_id != expected_account_id:
        raise InvalidPurchaseToken(
            f"Obfuscated account id mismatch for user {user.id} "
            f"(purchase token belongs to a different session)"
        )

    subscription = PlayBillingSubscription.objects.create(
        user=user,
        plan=plan,
        purchase_token=purchase_token,
        order_id=verified.get("latestOrderId", ""),
    )
    sync_subscription_state(subscription, verified)

    from .cross_provider import get_live_subscription
    live = get_live_subscription(user)
    if live and live.provider == "razorpay":
        logger.warning(
            "User %s completed a Play purchase while a Razorpay subscription "
            "(%s, status=%s) is still live -- manual reconciliation needed.",
            user.id, live.subscription.razorpay_subscription_id, live.status,
        )

    try:
        acknowledge_subscription_purchase(settings.GOOGLE_PLAY_PACKAGE_NAME, purchase_token)
    except HttpError as e:
        logger.error("Acknowledge call failed for user %s: %s", user.id, e)

    return subscription
