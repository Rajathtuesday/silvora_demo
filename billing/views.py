# billing/views.py
import logging

import requests
from django.conf import settings
from django.core.mail import send_mail
from django.core.signing import BadSignature, SignatureExpired
from django.shortcuts import render
from django.utils import timezone
from datetime import datetime, timedelta, timezone as dt_timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions

from users.models import User, UserQuota
from .models import RazorpayPlan, Subscription
from .serializers import CreateSubscriptionSerializer
from .services.razorpay_client import verify_webhook_signature
from .services.subscription_service import PlanNotConfigured, create_user_subscription
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
    elsewhere. No per-event idempotency tracking is needed here the way a
    payment-ledger webhook would need it: every handler below either flips a
    status field or calls UserQuota.set_tier(), and both are naturally
    idempotent — applying the same event twice lands on the same end state,
    not a duplicated side effect.
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
            # plan notes. Best-effort notify only.
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

        elif event in ("subscription.cancelled", "subscription.completed"):
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
