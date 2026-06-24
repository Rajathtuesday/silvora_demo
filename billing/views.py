# billing/views.py
import logging

import requests
from django.conf import settings
from django.core.mail import send_mail
from datetime import datetime, timezone as dt_timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions

from users.models import SubscriptionTier, UserQuota
from .models import RazorpayPlan, Subscription
from .serializers import CreateSubscriptionSerializer
from .services.razorpay_client import create_subscription, verify_webhook_signature

logger = logging.getLogger("silvora.billing")


class CreateSubscriptionView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = "billing"

    def post(self, request):
        serializer = CreateSubscriptionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        tier = serializer.validated_data["tier"]
        interval = serializer.validated_data["interval"]

        try:
            plan = RazorpayPlan.objects.get(tier=tier, interval=interval)
        except RazorpayPlan.DoesNotExist:
            return Response(
                {"error": "This plan is not configured yet. Please try again shortly."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            rzp_subscription = create_subscription(plan.razorpay_plan_id)
        except requests.RequestException as e:
            logger.error("Razorpay subscription creation failed for user %s: %s", request.user.id, e)
            return Response(
                {"error": "Could not reach Razorpay. Please try again."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        subscription = Subscription.objects.create(
            user=request.user,
            plan=plan,
            razorpay_subscription_id=rzp_subscription["id"],
            status=rzp_subscription.get("status", "created"),
        )

        return Response(
            {
                "subscription_id": subscription.razorpay_subscription_id,
                "razorpay_key_id": settings.RAZORPAY_KEY_ID,
                "status": subscription.status,
            },
            status=status.HTTP_201_CREATED,
        )


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
            subscription.status = "cancelled" if event == "subscription.cancelled" else "completed"
            subscription.save(update_fields=["status"])
            quota.set_tier(SubscriptionTier.FREE)

        return Response({"ok": True})
