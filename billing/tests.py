# billing/tests.py
import hashlib
import hmac
import json
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework import status

from tenants.models import Tenant
from files.models import FileRecord
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
        self.client.post("/api/auth/register/", {"email": self.email, "password": PW, "accepted_privacy_policy": True}, format="json")
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

    def test_cancelled_event_does_not_downgrade_immediately(self):
        """Cancellation starts a 7-day grace period instead of downgrading
        on the spot — the user keeps their paid limit until that elapses
        (see process_subscription_grace_periods for what actually acts on
        grace_ends_at/purge_at)."""
        self._post_webhook("subscription.activated", current_end=1893456000)
        mail.outbox = []
        res = self._post_webhook("subscription.cancelled")

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, "cancelled")
        self.assertIsNotNone(self.subscription.grace_ends_at)
        self.assertIsNotNone(self.subscription.purge_at)
        self.assertGreater(self.subscription.purge_at, self.subscription.grace_ends_at)

        quota = UserQuota.objects.get(user=self.user)
        self.assertEqual(quota.tier, SubscriptionTier.PRO)  # not downgraded yet

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("webhookuser@example.com", mail.outbox[0].to)

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


GiB = 1024 * 1024 * 1024


class ProcessSubscriptionGracePeriodsTests(TestCase):
    """The daily cron command — see process_subscription_grace_periods.py.
    Cancellation itself (tested above) only schedules these two dates;
    this command is what actually acts on them."""

    def setUp(self):
        mail.outbox = []
        self.tenant = Tenant.objects.create(name="grace_tenant", tenant_type=Tenant.TYPE_INDIVIDUAL)
        self.user = User.objects.create_user(
            username="grace@example.com", email="grace@example.com",
            password="x", tenant=self.tenant,
        )
        self.plan = RazorpayPlan.objects.create(
            tier=SubscriptionTier.PRO, interval=RazorpayPlan.Interval.MONTHLY,
            razorpay_plan_id="plan_grace", amount_paise=19900,
        )
        self.quota = UserQuota.objects.create(user=self.user, tier=SubscriptionTier.PRO, limit_bytes=100 * GiB)

    def _make_subscription(self, status="cancelled", grace_ends_at=None, purge_at=None):
        return Subscription.objects.create(
            user=self.user, plan=self.plan,
            razorpay_subscription_id=f"sub_{uuid.uuid4().hex[:12]}",
            status=status, grace_ends_at=grace_ends_at, purge_at=purge_at,
        )

    def _make_file(self, size, created_at):
        f = FileRecord.objects.create(
            id=uuid.uuid4(), owner=self.user, tenant=self.tenant,
            filename_ciphertext=b"x", filename_nonce=b"x", filename_mac=b"x",
            size=size, security_mode="zero_knowledge",
            storage_type=FileRecord.STORAGE_R2, upload_state=FileRecord.UploadState.COMMITTED,
        )
        # created_at is auto_now_add — backdate it directly for ordering tests.
        FileRecord.objects.filter(pk=f.pk).update(created_at=created_at)
        return f

    def test_downgrades_once_grace_period_has_elapsed(self):
        sub = self._make_subscription(grace_ends_at=timezone.now() - timedelta(hours=1))

        call_command("process_subscription_grace_periods")

        self.quota.refresh_from_db()
        self.assertEqual(self.quota.tier, SubscriptionTier.FREE)
        self.assertEqual(self.quota.limit_bytes, 1 * GiB)
        sub.refresh_from_db()
        self.assertIsNone(sub.grace_ends_at)  # cleared so it never re-fires
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("grace@example.com", mail.outbox[0].to)

    def test_grace_period_still_in_the_future_is_left_alone(self):
        sub = self._make_subscription(grace_ends_at=timezone.now() + timedelta(days=3))

        call_command("process_subscription_grace_periods")

        self.quota.refresh_from_db()
        self.assertEqual(self.quota.tier, SubscriptionTier.PRO)  # untouched
        sub.refresh_from_db()
        self.assertIsNotNone(sub.grace_ends_at)  # not yet acted on
        self.assertEqual(len(mail.outbox), 0)

    def test_downgrade_skipped_if_user_already_resubscribed(self):
        sub = self._make_subscription(grace_ends_at=timezone.now() - timedelta(hours=1))
        self._make_subscription(status="active")  # the resubscription

        call_command("process_subscription_grace_periods")

        self.quota.refresh_from_db()
        self.assertEqual(self.quota.tier, SubscriptionTier.PRO)  # NOT downgraded
        sub.refresh_from_db()
        self.assertIsNone(sub.grace_ends_at)  # still cleared, so it doesn't keep re-checking

    @patch("billing.management.commands.process_subscription_grace_periods.StorageGateway")
    def test_purge_deletes_oldest_files_first_down_to_the_limit(self, mock_storage_cls):
        mock_storage = mock_storage_cls.return_value
        self.quota.tier = SubscriptionTier.FREE
        self.quota.limit_bytes = 1 * GiB
        self.quota.used_bytes = int(1.5 * GiB)
        self.quota.save()

        now = timezone.now()
        oldest = self._make_file(size=int(0.6 * GiB), created_at=now - timedelta(days=10))
        middle = self._make_file(size=int(0.6 * GiB), created_at=now - timedelta(days=5))
        newest = self._make_file(size=int(0.3 * GiB), created_at=now - timedelta(days=1))
        sub = self._make_subscription(status="cancelled", purge_at=now - timedelta(hours=1))

        call_command("process_subscription_grace_periods")

        # Oldest deleted first; stop as soon as we're back under the limit.
        self.assertFalse(FileRecord.objects.filter(pk=oldest.pk).exists())
        self.assertTrue(FileRecord.objects.filter(pk=middle.pk).exists())
        self.assertTrue(FileRecord.objects.filter(pk=newest.pk).exists())
        mock_storage.delete_recursive.assert_called_once()

        self.quota.refresh_from_db()
        self.assertEqual(self.quota.used_bytes, int(1.5 * GiB) - int(0.6 * GiB))  # the oldest file's size released

        sub.refresh_from_db()
        self.assertIsNone(sub.purge_at)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("removed", mail.outbox[0].subject.lower())

    @patch("billing.management.commands.process_subscription_grace_periods.StorageGateway")
    def test_purge_is_a_noop_if_already_under_the_limit(self, mock_storage_cls):
        self.quota.tier = SubscriptionTier.FREE
        self.quota.limit_bytes = 1 * GiB
        self.quota.used_bytes = int(0.5 * GiB)
        self.quota.save()
        self._make_file(size=int(0.5 * GiB), created_at=timezone.now() - timedelta(days=10))
        self._make_subscription(status="cancelled", purge_at=timezone.now() - timedelta(hours=1))

        call_command("process_subscription_grace_periods")

        self.assertEqual(FileRecord.objects.count(), 1)  # nothing deleted
        mock_storage_cls.return_value.delete_recursive.assert_not_called()
        self.assertEqual(len(mail.outbox), 0)  # no "files removed" email for a no-op
