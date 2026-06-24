# billing/tests.py
import hashlib
import hmac
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APITestCase
from rest_framework import status

from users.models import SubscriptionTier, UserQuota
from .models import RazorpayPlan, Subscription

User = get_user_model()

WEBHOOK_SECRET = "test_webhook_secret"
SUBSCRIBE_URL = "/api/billing/subscribe/"
WEBHOOK_URL = "/api/billing/webhook/"
PW = "Str0ng!Vault#Key2026"


def _sign(body_bytes, secret=WEBHOOK_SECRET):
    return hmac.new(secret.encode(), body_bytes, hashlib.sha256).hexdigest()


class CreateSubscriptionViewTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.email = "subscriber@example.com"
        self.client.post("/api/auth/register/", {"email": self.email, "password": PW}, format="json")
        cache.clear()
        res = self.client.post("/api/auth/token/", {"username": self.email, "password": PW}, format="json")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.json()['access']}")
        self.user = User.objects.get(email=self.email)

        self.plan = RazorpayPlan.objects.create(
            tier=SubscriptionTier.PRO, interval=RazorpayPlan.Interval.MONTHLY,
            razorpay_plan_id="plan_test123", amount_paise=19900,
        )

    def test_requires_authentication(self):
        self.client.credentials()
        res = self.client.post(SUBSCRIBE_URL, {"tier": "pro", "interval": "monthly"}, format="json")
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_rejects_unknown_tier(self):
        res = self.client.post(SUBSCRIBE_URL, {"tier": "godmode", "interval": "monthly"}, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_rejects_a_tier_with_no_configured_plan(self):
        res = self.client.post(SUBSCRIBE_URL, {"tier": "enterprise", "interval": "yearly"}, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("billing.views.create_subscription")
    def test_creates_a_local_subscription_row(self, mock_create):
        mock_create.return_value = {"id": "sub_test456", "status": "created"}

        res = self.client.post(SUBSCRIBE_URL, {"tier": "pro", "interval": "monthly"}, format="json")

        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.json()["subscription_id"], "sub_test456")
        sub = Subscription.objects.get(razorpay_subscription_id="sub_test456")
        self.assertEqual(sub.user, self.user)
        self.assertEqual(sub.plan, self.plan)

    @patch("billing.views.create_subscription")
    def test_razorpay_failure_returns_clean_error_not_a_500(self, mock_create):
        import requests
        mock_create.side_effect = requests.RequestException("boom")

        res = self.client.post(SUBSCRIBE_URL, {"tier": "pro", "interval": "monthly"}, format="json")

        self.assertEqual(res.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(Subscription.objects.count(), 0)


@override_settings(RAZORPAY_WEBHOOK_SECRET=WEBHOOK_SECRET)
class RazorpaySubscriptionWebhookTests(APITestCase):
    def setUp(self):
        cache.clear()
        mail.outbox = []
        self.user = User.objects.create_user(username="webhookuser@example.com", email="webhookuser@example.com", password=PW)
        self.plan = RazorpayPlan.objects.create(
            tier=SubscriptionTier.PRO, interval=RazorpayPlan.Interval.MONTHLY,
            razorpay_plan_id="plan_test123", amount_paise=19900,
        )
        self.subscription = Subscription.objects.create(
            user=self.user, plan=self.plan, razorpay_subscription_id="sub_abc", status="created",
        )

    def _post_webhook(self, event, current_end=None):
        entity = {"id": "sub_abc"}
        if current_end:
            entity["current_end"] = current_end
        payload = {"event": event, "payload": {"subscription": {"entity": entity}}}
        body = json.dumps(payload).encode()
        return self.client.post(
            WEBHOOK_URL, data=body, content_type="application/json",
            HTTP_X_RAZORPAY_SIGNATURE=_sign(body),
        )

    def test_invalid_signature_rejected(self):
        body = json.dumps({"event": "subscription.activated", "payload": {}}).encode()
        res = self.client.post(
            WEBHOOK_URL, data=body, content_type="application/json",
            HTTP_X_RAZORPAY_SIGNATURE="wrong-signature",
        )
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_unknown_subscription_id_does_not_crash(self):
        payload = {"event": "subscription.activated", "payload": {"subscription": {"entity": {"id": "sub_does_not_exist"}}}}
        body = json.dumps(payload).encode()
        res = self.client.post(
            WEBHOOK_URL, data=body, content_type="application/json",
            HTTP_X_RAZORPAY_SIGNATURE=_sign(body),
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_activated_event_upgrades_quota_to_plan_tier(self):
        res = self._post_webhook("subscription.activated", current_end=1893456000)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, "active")
        self.assertIsNotNone(self.subscription.current_period_end)

        quota = UserQuota.objects.get(user=self.user)
        self.assertEqual(quota.tier, SubscriptionTier.PRO)
        self.assertEqual(quota.limit_bytes, 100 * 1024 * 1024 * 1024)

    def test_charged_event_extends_period_without_changing_tier_again(self):
        self._post_webhook("subscription.activated", current_end=1893456000)
        res = self._post_webhook("subscription.charged", current_end=1896134400)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.current_period_end.timestamp(), 1896134400)

    def test_cancelled_event_downgrades_to_free(self):
        self._post_webhook("subscription.activated", current_end=1893456000)
        res = self._post_webhook("subscription.cancelled")

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, "cancelled")

        quota = UserQuota.objects.get(user=self.user)
        self.assertEqual(quota.tier, SubscriptionTier.FREE)
        self.assertEqual(quota.limit_bytes, 1 * 1024 * 1024 * 1024)

    def test_payment_failed_sends_notice_without_downgrading(self):
        self._post_webhook("subscription.activated", current_end=1893456000)
        mail.outbox = []  # isolate from the activation flow's own emails (there are none, but be explicit)

        res = self._post_webhook("payment.failed")

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("webhookuser@example.com", mail.outbox[0].to)

        quota = UserQuota.objects.get(user=self.user)
        self.assertEqual(quota.tier, SubscriptionTier.PRO)  # not downgraded

    def test_replaying_activated_event_is_idempotent(self):
        self._post_webhook("subscription.activated", current_end=1893456000)
        self._post_webhook("subscription.activated", current_end=1893456000)

        quota = UserQuota.objects.get(user=self.user)
        self.assertEqual(quota.tier, SubscriptionTier.PRO)  # same end state, not doubled
