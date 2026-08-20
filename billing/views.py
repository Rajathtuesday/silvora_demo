# billing/views.py
import base64
import json
import logging

import requests
from django.conf import settings
from django.core.mail import send_mail
from django.core.signing import BadSignature, SignatureExpired
from django.shortcuts import render
from django.utils import timezone
from datetime import datetime, timedelta, timezone as dt_timezone
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from googleapiclient.errors import HttpError
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions

from users.models import User, UserQuota
from .models import PlayBillingSubscription, RazorpayPlan, Subscription
from .serializers import CreateSubscriptionSerializer, VerifyPlayPurchaseSerializer
from .services.obfuscated_account import make_obfuscated_account_id
from .services.play_billing_client import get_subscription_purchase
from .services.play_purchase_service import (
    InvalidPurchaseToken,
    PlanNotConfigured as PlayPlanNotConfigured,
    sync_subscription_state,
    verify_and_grant_play_purchase,
)
from .services.razorpay_client import verify_webhook_signature
from .services.subscription_service import AlreadySubscribed, PlanNotConfigured, create_user_subscription
from .services.web_link import make_billing_web_token, unsign_billing_web_token

logger = logging.getLogger("silvora.billing")


class WebBillingLinkView(APIView):
    """
    Subscribing happens entirely on silvora.cloud now, not inside the app --
    Google Play requires in-app digital subscriptions to go through Play
    Billing, and routing through Razorpay's own checkout on the web instead
    sidesteps that requirement entirely (same approach most cloud-storage
    apps use on Android). The app's only job is to ask for a signed link to
    the right checkout page, already identifying the user, and open it
    externally.
    """
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = "billing"

    def get(self, request):
        serializer = CreateSubscriptionSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        tier = serializer.validated_data["tier"]
        interval = serializer.validated_data["interval"]

        token = make_billing_web_token(request.user)
        url = f"{settings.SITE_BASE_URL}/billing/checkout/?token={token}&tier={tier}&interval={interval}"
        return Response({"url": url})


def billing_checkout_page(request):
    """
    Public page (the token, not a session, is the credential) -- creates the
    Razorpay subscription server-side and renders Razorpay's web Checkout.js
    for it. The actual tier upgrade still happens later, via the webhook,
    independent of this page -- same as the in-app flow always worked.
    """
    token = request.GET.get("token", "")
    tier = request.GET.get("tier", "")
    interval = request.GET.get("interval", "")

    try:
        user_id = unsign_billing_web_token(token)
    except SignatureExpired:
        return render(request, "billing/checkout_error.html", {
            "message": "This link has expired. Go back to the Silvora app and tap Manage Subscription again.",
        }, status=400)
    except BadSignature:
        return render(request, "billing/checkout_error.html", {
            "message": "This link isn't valid. Go back to the Silvora app and tap Manage Subscription again.",
        }, status=400)

    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return render(request, "billing/checkout_error.html", {
            "message": "This link isn't valid. Go back to the Silvora app and tap Manage Subscription again.",
        }, status=400)

    try:
        subscription = create_user_subscription(user, tier, interval)
    except AlreadySubscribed as e:
        return render(request, "billing/checkout_error.html", {
            "message": (
                f"You already have a {e.subscription.status} subscription. "
                "Manage or cancel your existing plan before starting a new one."
            ),
        }, status=409)
    except PlanNotConfigured:
        return render(request, "billing/checkout_error.html", {
            "message": "This plan isn't configured yet. Please try again shortly.",
        }, status=400)
    except requests.RequestException as e:
        logger.error("Razorpay subscription creation failed for user %s: %s", user.id, e)
        return render(request, "billing/checkout_error.html", {
            "message": "Couldn't reach Razorpay. Please try again.",
        }, status=502)

    return render(request, "billing/checkout.html", {
        "subscription_id": subscription.razorpay_subscription_id,
        "razorpay_key_id": settings.RAZORPAY_KEY_ID,
        "tier": tier,
        "interval": interval,
    })


class RazorpaySubscriptionWebhookView(APIView):
    """
    Public — Razorpay calls this directly, no user session. The signature is
    the only trust boundary, same pattern as the QR-payment webhook
    elsewhere. No per-event idempotency table is needed here the way a
    payment-ledger webhook would need it: subscription.activated/charged
    each flip a status field or call UserQuota.set_tier(), naturally
    idempotent on their own. cancelled/completed and payment.failed are
    different — they have a real side effect (an email) and mutable date
    fields a naive handler would repeat on every redelivery, so those two
    guard explicitly (a terminal-status check, and a notification
    timestamp, respectively) before acting.
    """

    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        signature = request.headers.get("X-Razorpay-Signature", "")
        if not verify_webhook_signature(request.body, signature, settings.RAZORPAY_WEBHOOK_SECRET):
            return Response({"error": "Invalid signature"}, status=status.HTTP_401_UNAUTHORIZED)

        payload = request.data
        event = payload.get("event", "")
        entity = payload.get("payload", {}).get("subscription", {}).get("entity", {}) or {}
        razorpay_subscription_id = entity.get("id")

        if not razorpay_subscription_id:
            # Not every Razorpay webhook event carries a subscription entity
            # (e.g. plain payment events) — nothing for this view to do.
            return Response({"ok": True})

        try:
            subscription = Subscription.objects.select_related("plan", "user").get(
                razorpay_subscription_id=razorpay_subscription_id
            )
        except Subscription.DoesNotExist:
            logger.warning("Webhook for unknown subscription_id=%s (event=%s)", razorpay_subscription_id, event)
            return Response({"ok": True})

        quota, _ = UserQuota.objects.get_or_create(user=subscription.user)

        if event == "subscription.activated":
            subscription.status = "active"
            current_end = entity.get("current_end")
            if current_end:
                subscription.current_period_end = datetime.fromtimestamp(current_end, tz=dt_timezone.utc)
            subscription.save(update_fields=["status", "current_period_end"])
            quota.set_tier(subscription.plan.tier)

        elif event == "subscription.charged":
            current_end = entity.get("current_end")
            if current_end:
                subscription.current_period_end = datetime.fromtimestamp(current_end, tz=dt_timezone.utc)
                subscription.save(update_fields=["current_period_end"])
            # Renewal succeeded — tier is already correct from activation;
            # nothing else to flip here.

        elif event == "payment.failed":
            # Razorpay retries failed subscription charges on its own dunning
            # schedule. Deliberately NOT downgrading immediately here — see
            # plan notes. Best-effort notify only — but at most once per 24h,
            # so webhook redelivery of the SAME failure can't resend this an
            # unbounded number of times (a genuinely new failure is always
            # at least one billing cycle away, so this window can never
            # swallow a real one).
            now = timezone.now()
            already_notified_recently = (
                subscription.last_payment_failed_notified_at is not None
                and now - subscription.last_payment_failed_notified_at < timedelta(hours=24)
            )
            if not already_notified_recently:
                user = subscription.user
                if user.email:
                    try:
                        send_mail(
                            subject="Silvora payment failed",
                            message=(
                                "Your recent Silvora subscription payment didn't go through. "
                                "Razorpay will retry automatically — please make sure your "
                                "payment method is up to date to avoid any interruption."
                            ),
                            from_email=settings.DEFAULT_FROM_EMAIL,
                            recipient_list=[user.email],
                            fail_silently=True,
                        )
                    except Exception as e:
                        logger.error("Failed to send payment-failed notice to user %s: %s", user.id, e)
                subscription.last_payment_failed_notified_at = now
                subscription.save(update_fields=["last_payment_failed_notified_at"])

        elif event in ("subscription.cancelled", "subscription.completed"):
            # Idempotent against redelivery: once this subscription has
            # already landed in EITHER terminal state, a redelivered
            # cancelled/completed event is a pure duplicate. Without this,
            # grace_ends_at/purge_at get pushed further into the future on
            # every retry and the "subscription ended" email resends every time.
            if subscription.status in ("cancelled", "completed"):
                return Response({"ok": True})

            # Deliberately NOT downgrading immediately. The user keeps their
            # paid limit for 7 days (real time to download anything over the
            # free tier), then gets downgraded to Free, then gets a further
            # 23 days before any file actually gets deleted. See
            # process_subscription_grace_periods for the steps that act on
            # these two dates.
            now = timezone.now()
            subscription.status = "cancelled" if event == "subscription.cancelled" else "completed"
            subscription.grace_ends_at = now + timedelta(days=7)
            subscription.purge_at = now + timedelta(days=30)
            subscription.save(update_fields=["status", "grace_ends_at", "purge_at"])

            user = subscription.user
            if user.email:
                try:
                    send_mail(
                        subject="Your Silvora subscription has ended",
                        message=(
                            "Your Silvora subscription has ended. You'll keep your current "
                            "storage limit for 7 more days — plenty of time to download "
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
                    logger.error("Failed to send cancellation notice to user %s: %s", user.id, e)

        return Response({"ok": True})


class PlayObfuscatedAccountIdView(APIView):
    """
    The app fetches this once before launching a Play purchase and passes it
    as applicationUserName on GooglePlayPurchaseParam. Play echoes it back
    verifiably in the server-side purchase response, which is what lets
    PlayPurchaseVerifyView confirm a submitted purchase token actually
    belongs to the calling session -- see services/obfuscated_account.py.
    """
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = "billing"

    def get(self, request):
        return Response({"obfuscated_account_id": make_obfuscated_account_id(request.user)})


class PlayPurchaseVerifyView(APIView):
    """
    The app calls this right after a Play purchase completes, with the raw
    purchase token. Never trusts anything the client reports beyond the
    token itself -- verify_and_grant_play_purchase re-derives the product,
    tier, and account ownership entirely from Google's own verified API
    response before granting anything.
    """
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = "billing"

    def post(self, request):
        serializer = VerifyPlayPurchaseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            subscription = verify_and_grant_play_purchase(
                request.user,
                serializer.validated_data["product_id"],
                serializer.validated_data["purchase_token"],
            )
        except InvalidPurchaseToken as e:
            logger.warning("Rejected Play purchase for user %s: %s", request.user.id, e)
            return Response({"error": "This purchase couldn't be verified."}, status=status.HTTP_400_BAD_REQUEST)
        except PlayPlanNotConfigured as e:
            logger.error("Play purchase verify failed, plan not configured: %s", e)
            return Response({"error": "This plan isn't configured yet. Please try again shortly."}, status=status.HTTP_400_BAD_REQUEST)
        except HttpError as e:
            logger.error("Google Play API call failed for user %s: %s", request.user.id, e)
            return Response({"error": "Couldn't reach Google Play. Please try again."}, status=status.HTTP_502_BAD_GATEWAY)

        return Response({
            "tier": subscription.plan.tier,
            "status": subscription.status,
            "current_period_end": subscription.current_period_end,
        })


class PlayRTDNWebhookView(APIView):
    """
    Public -- Google Cloud Pub/Sub calls this directly (push subscription),
    no user session. Unlike the Razorpay webhook's HMAC header, the trust
    boundary here is an OIDC bearer token issued by Pub/Sub itself, verified
    against Google's public keys, then checked against the specific service
    account configured on the push subscription -- binds trust to that one
    identity, not just "any Google-signed token."

    State is always re-derived from a fresh call to Google's own API rather
    than trusted from the notification payload -- Google's own guidance is
    that RTDN is a "something changed, go re-fetch" signal, not a data
    payload to trust directly.
    """
    authentication_classes = []
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return Response({"error": "Missing bearer token"}, status=status.HTTP_401_UNAUTHORIZED)
        token = auth_header[len("Bearer "):]

        try:
            claims = google_id_token.verify_oauth2_token(
                token, google_requests.Request(), audience=settings.PUBSUB_PUSH_AUDIENCE
            )
        except ValueError as e:
            logger.warning("Rejected RTDN webhook call, bad OIDC token: %s", e)
            return Response({"error": "Invalid token"}, status=status.HTTP_401_UNAUTHORIZED)

        if not claims.get("email_verified") or claims.get("email") != settings.PUBSUB_PUSH_SERVICE_ACCOUNT_EMAIL:
            logger.warning("Rejected RTDN webhook call, unexpected service account: %s", claims.get("email"))
            return Response({"error": "Unauthorized service account"}, status=status.HTTP_401_UNAUTHORIZED)

        try:
            envelope = json.loads(request.body)
            message_data = envelope["message"]["data"]
            rtdn = json.loads(base64.b64decode(message_data))
        except (KeyError, ValueError, TypeError) as e:
            logger.error("Malformed Pub/Sub push envelope: %s", e)
            # 200, not 400 -- a malformed envelope will never parse correctly
            # on retry either; asking Pub/Sub to keep retrying it forever
            # only piles up redeliveries for nothing.
            return Response({"ok": True})

        if "testNotification" in rtdn:
            # Exactly what Play Console's "send test notification" button
            # exercises to confirm the endpoint before anything else is wired.
            return Response({"ok": True})

        notification = rtdn.get("subscriptionNotification")
        if not notification:
            # oneTimeProductNotification / voucherNotification -- Silvora
            # only sells auto-renewing subscriptions, nothing to do here.
            return Response({"ok": True})

        purchase_token = notification.get("purchaseToken")
        if not purchase_token:
            return Response({"ok": True})

        subscription = PlayBillingSubscription.objects.filter(purchase_token=purchase_token).select_related("user", "plan").first()

        if subscription is None:
            # SUBSCRIPTION_PURCHASED racing against the client's own verify
            # call (see PlayPurchaseVerifyView) is expected and not an
            # error -- the row may simply not exist yet. Anything else with
            # no local row is genuinely unknown, log and no-op, same as the
            # Razorpay webhook's "unknown subscription_id" handling. Either
            # way there's no local row to reconcile against, so this never
            # calls Google's API at all -- no reason to spend that call on a
            # token we're just going to discard.
            notification_type = notification.get("notificationType")
            if notification_type != 4:  # SUBSCRIPTION_PURCHASED
                logger.warning("RTDN for unknown purchase_token=%s (type=%s)", purchase_token, notification_type)
                return Response({"ok": True})

            # Google's RTDN payload for SUBSCRIPTION_PURCHASED carries no
            # user-identifying field at all -- only the obfuscated account
            # id (a one-way HMAC, not a reverse lookup key) and the purchase
            # token. Creating a PlayBillingSubscription row requires knowing
            # WHICH local user it belongs to, which only
            # PlayPurchaseVerifyView's request.user can supply. In practice
            # this race is closed by that view running synchronously right
            # after the purchase completes, before Google's RTDN has any
            # reason to have fired yet. If this branch is ever actually hit,
            # it means verify never ran (e.g. the app crashed right after
            # purchase) -- log loudly so it surfaces as a real gap rather
            # than fail silently or guess at a user.
            logger.error(
                "RTDN SUBSCRIPTION_PURCHASED with no matching local row for "
                "token=%s -- the client-side verify call likely never "
                "completed. Needs manual reconciliation.",
                purchase_token,
            )
            return Response({"ok": True})

        try:
            verified = get_subscription_purchase(settings.GOOGLE_PLAY_PACKAGE_NAME, purchase_token)
        except HttpError as e:
            logger.error("RTDN: failed to re-fetch purchase state for token=%s: %s", purchase_token, e)
            # 200 anyway -- a transient Google-side failure will be retried
            # by the next real event (renewal, expiry, etc) regardless.
            return Response({"ok": True})

        sync_subscription_state(subscription, verified)
        return Response({"ok": True})
