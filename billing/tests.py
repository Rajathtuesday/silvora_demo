# billing/tests.py
import base64
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
from googleapiclient.errors import HttpError
from rest_framework.test import APITestCase
from rest_framework import status

from tenants.models import Tenant
from files.models import FileRecord
from users.models import SubscriptionTier, UserQuota
from .models import PlayBillingPlan, PlayBillingSubscription, RazorpayPlan, Subscription
from .services.obfuscated_account import make_obfuscated_account_id

User = get_user_model()

WEBHOOK_SECRET = "test_webhook_secret"
WEB_LINK_URL = "/api/billing/web-link/"
WEBHOOK_URL = "/api/billing/webhook/"
PW = "Str0ng!Vault#Key2026"


def _sign(body_bytes, secret=WEBHOOK_SECRET):
    return hmac.new(secret.encode(), body_bytes, hashlib.sha256).hexdigest()


class WebBillingLinkViewTests(APITestCase):
    """Subscribing happens on the website now, not in-app (Google Play
    requires in-app digital subscriptions to go through Play Billing) --
    this endpoint's only job is handing back a signed link to there."""

    def setUp(self):
        cache.clear()
        self.email = "subscriber@example.com"
        self.client.post("/api/auth/register/", {"email": self.email, "password": PW, "accepted_privacy_policy": True}, format="json")
        cache.clear()
        res = self.client.post("/api/auth/token/", {"username": self.email, "password": PW}, format="json")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.json()['access']}")
        self.user = User.objects.get(email=self.email)

    def test_requires_authentication(self):
        self.client.credentials()
        res = self.client.get(WEB_LINK_URL, {"tier": "pro", "interval": "monthly"})
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_rejects_unknown_tier(self):
        res = self.client.get(WEB_LINK_URL, {"tier": "godmode", "interval": "monthly"})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_returns_a_signed_checkout_url(self):
        res = self.client.get(WEB_LINK_URL, {"tier": "pro", "interval": "monthly"})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        url = res.json()["url"]
        self.assertIn("/billing/checkout/?token=", url)
        self.assertIn("tier=pro", url)
        self.assertIn("interval=monthly", url)
        # No Subscription row yet -- creation happens on the checkout page,
        # not here. This endpoint only ever hands back a link.
        self.assertEqual(Subscription.objects.count(), 0)


class BillingCheckoutPageTests(TestCase):
    """The actual Razorpay subscription gets created here, when the signed
    link is opened -- not when the app asked for the link."""

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="checkout@example.com", email="checkout@example.com", password=PW,
        )
        self.plan = RazorpayPlan.objects.create(
            tier=SubscriptionTier.PRO, interval=RazorpayPlan.Interval.MONTHLY,
            razorpay_plan_id="plan_test123", amount_paise=19900,
        )

    def _token(self):
        from .services.web_link import make_billing_web_token
        return make_billing_web_token(self.user)

    def test_invalid_token_rejected(self):
        res = self.client.get("/billing/checkout/", {"token": "garbage", "tier": "pro", "interval": "monthly"})
        self.assertEqual(res.status_code, 400)

    def test_unconfigured_plan_shows_clean_error(self):
        res = self.client.get("/billing/checkout/", {"token": self._token(), "tier": "enterprise", "interval": "yearly"})
        self.assertEqual(res.status_code, 400)
        self.assertContains(res, "configured yet", status_code=400)

    @patch("billing.services.subscription_service.create_subscription")
    def test_valid_token_creates_subscription_and_renders_checkout(self, mock_create):
        mock_create.return_value = {"id": "sub_test456", "status": "created"}

        res = self.client.get("/billing/checkout/", {"token": self._token(), "tier": "pro", "interval": "monthly"})

        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "sub_test456")
        sub = Subscription.objects.get(razorpay_subscription_id="sub_test456")
        self.assertEqual(sub.user, self.user)
        self.assertEqual(sub.plan, self.plan)

    @patch("billing.services.subscription_service.create_subscription")
    def test_razorpay_failure_shows_clean_error_not_a_500(self, mock_create):
        import requests
        mock_create.side_effect = requests.RequestException("boom")

        res = self.client.get("/billing/checkout/", {"token": self._token(), "tier": "pro", "interval": "monthly"})

        self.assertEqual(res.status_code, 502)
        self.assertEqual(Subscription.objects.count(), 0)

    @patch("billing.services.subscription_service.create_subscription")
    def test_reloading_checkout_page_reuses_pending_subscription(self, mock_create):
        mock_create.return_value = {"id": "sub_test456", "status": "created"}
        token = self._token()

        res1 = self.client.get("/billing/checkout/", {"token": token, "tier": "pro", "interval": "monthly"})
        res2 = self.client.get("/billing/checkout/", {"token": token, "tier": "pro", "interval": "monthly"})

        self.assertEqual(res1.status_code, 200)
        self.assertEqual(res2.status_code, 200)
        mock_create.assert_called_once()  # Razorpay hit exactly once
        self.assertEqual(Subscription.objects.count(), 1)
        self.assertContains(res2, "sub_test456")

    @patch("billing.services.subscription_service.create_subscription")
    def test_checkout_rejects_when_user_already_has_a_live_subscription(self, mock_create):
        Subscription.objects.create(
            user=self.user, plan=self.plan, razorpay_subscription_id="sub_existing", status="active",
        )

        res = self.client.get("/billing/checkout/", {"token": self._token(), "tier": "pro", "interval": "monthly"})

        self.assertEqual(res.status_code, 409)
        mock_create.assert_not_called()
        self.assertEqual(Subscription.objects.count(), 1)


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

    def test_replaying_cancelled_event_is_idempotent(self):
        self._post_webhook("subscription.activated", current_end=1893456000)
        mail.outbox = []
        self._post_webhook("subscription.cancelled")
        self.subscription.refresh_from_db()
        first_grace, first_purge = self.subscription.grace_ends_at, self.subscription.purge_at

        res = self._post_webhook("subscription.cancelled")

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.grace_ends_at, first_grace)  # not pushed further out
        self.assertEqual(self.subscription.purge_at, first_purge)
        self.assertEqual(len(mail.outbox), 1)  # still just the one email

    def test_completed_event_after_already_cancelled_is_a_noop(self):
        """Guard is 'already in a terminal state', not 'this exact event
        name already ran' -- a completed redelivered after cancelled must
        not re-fire either."""
        self._post_webhook("subscription.activated", current_end=1893456000)
        self._post_webhook("subscription.cancelled")
        self.subscription.refresh_from_db()
        first_grace = self.subscription.grace_ends_at
        mail.outbox = []

        self._post_webhook("subscription.completed")

        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, "cancelled")  # not overwritten
        self.assertEqual(self.subscription.grace_ends_at, first_grace)
        self.assertEqual(len(mail.outbox), 0)

    def test_replaying_payment_failed_within_window_sends_one_email(self):
        self._post_webhook("subscription.activated", current_end=1893456000)
        mail.outbox = []
        self._post_webhook("payment.failed")
        res = self._post_webhook("payment.failed")  # immediate redelivery

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 1)

    def test_payment_failed_notifies_again_after_window_elapses(self):
        self._post_webhook("subscription.activated", current_end=1893456000)
        mail.outbox = []
        self._post_webhook("payment.failed")
        self.subscription.refresh_from_db()
        self.subscription.last_payment_failed_notified_at = timezone.now() - timedelta(hours=25)
        self.subscription.save(update_fields=["last_payment_failed_notified_at"])

        self._post_webhook("payment.failed")

        self.assertEqual(len(mail.outbox), 2)


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

    def test_razorpay_downgrade_skipped_when_user_has_an_active_play_subscription(self):
        """The cross-provider guard: a user who cancelled on Razorpay but has
        since gone active on Play must not be downgraded just because the
        Razorpay row's grace period elapsed."""
        play_plan = PlayBillingPlan.objects.create(
            tier=SubscriptionTier.PRO, interval=RazorpayPlan.Interval.MONTHLY,
            play_product_id="silvora_pro", play_base_plan_id="pro-monthly",
        )
        PlayBillingSubscription.objects.create(
            user=self.user, plan=play_plan, purchase_token="tok_active",
            status="SUBSCRIPTION_STATE_ACTIVE",
        )
        sub = self._make_subscription(grace_ends_at=timezone.now() - timedelta(hours=1))

        call_command("process_subscription_grace_periods")

        self.quota.refresh_from_db()
        self.assertEqual(self.quota.tier, SubscriptionTier.PRO)  # not downgraded
        sub.refresh_from_db()
        self.assertIsNone(sub.grace_ends_at)  # still cleared, acted on either way


PACKAGE_NAME = "cloud.silvora.app"
ACCOUNT_ID_URL = "/api/billing/play/account-id/"
PLAY_VERIFY_URL = "/api/billing/play/verify/"
RTDN_URL = "/api/billing/play/rtdn/"


def _verified_purchase(
    product_id="silvora_pro",
    base_plan_id="pro-monthly",
    state="SUBSCRIPTION_STATE_ACTIVE",
    obfuscated_account_id=None,
    order_id="GPA.1234-5678-9012-34567",
    expiry_time="2027-01-01T00:00:00Z",
):
    return {
        "subscriptionState": state,
        "latestOrderId": order_id,
        "lineItems": [{
            "productId": product_id,
            "offerDetails": {"basePlanId": base_plan_id},
            "expiryTime": expiry_time,
        }],
        "externalAccountIdentifiers": {
            "obfuscatedExternalAccountId": obfuscated_account_id or "",
        },
    }


def _rtdn_envelope(notification_type, purchase_token, subscription_id="silvora_pro"):
    rtdn = {
        "packageName": PACKAGE_NAME,
        "subscriptionNotification": {
            "notificationType": notification_type,
            "purchaseToken": purchase_token,
            "subscriptionId": subscription_id,
        },
    }
    data_b64 = base64.b64encode(json.dumps(rtdn).encode()).decode()
    return json.dumps({"message": {"data": data_b64}}).encode()


@override_settings(GOOGLE_PLAY_PACKAGE_NAME=PACKAGE_NAME)
class PlayObfuscatedAccountIdViewTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.email = "playuser@example.com"
        self.client.post("/api/auth/register/", {"email": self.email, "password": PW, "accepted_privacy_policy": True}, format="json")
        cache.clear()
        res = self.client.post("/api/auth/token/", {"username": self.email, "password": PW}, format="json")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.json()['access']}")
        self.user = User.objects.get(email=self.email)

    def test_requires_authentication(self):
        self.client.credentials()
        res = self.client.get(ACCOUNT_ID_URL)
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_returns_a_deterministic_id_matching_the_service_function(self):
        res = self.client.get(ACCOUNT_ID_URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.json()["obfuscated_account_id"], make_obfuscated_account_id(self.user))


@override_settings(GOOGLE_PLAY_PACKAGE_NAME=PACKAGE_NAME)
class PlayPurchaseVerifyViewTests(APITestCase):
    def setUp(self):
        cache.clear()
        mail.outbox = []
        self.email = "playbuyer@example.com"
        self.client.post("/api/auth/register/", {"email": self.email, "password": PW, "accepted_privacy_policy": True}, format="json")
        cache.clear()
        res = self.client.post("/api/auth/token/", {"username": self.email, "password": PW}, format="json")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {res.json()['access']}")
        self.user = User.objects.get(email=self.email)
        self.plan = PlayBillingPlan.objects.create(
            tier=SubscriptionTier.PRO, interval=RazorpayPlan.Interval.MONTHLY,
            play_product_id="silvora_pro", play_base_plan_id="pro-monthly",
        )

    def test_requires_authentication(self):
        self.client.credentials()
        res = self.client.post(PLAY_VERIFY_URL, {"purchase_token": "tok1", "product_id": "silvora_pro"})
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    @patch("billing.services.play_purchase_service.get_subscription_purchase")
    @patch("billing.services.play_purchase_service.acknowledge_subscription_purchase")
    def test_valid_purchase_grants_the_tier(self, mock_ack, mock_get):
        mock_get.return_value = _verified_purchase(
            obfuscated_account_id=make_obfuscated_account_id(self.user)
        )

        res = self.client.post(PLAY_VERIFY_URL, {"purchase_token": "tok1", "product_id": "silvora_pro"})

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.json()["tier"], "pro")
        quota = UserQuota.objects.get(user=self.user)
        self.assertEqual(quota.tier, SubscriptionTier.PRO)
        self.assertEqual(quota.limit_bytes, 100 * 1024 * 1024 * 1024)
        sub = PlayBillingSubscription.objects.get(purchase_token="tok1")
        self.assertEqual(sub.user, self.user)
        self.assertEqual(sub.status, "SUBSCRIPTION_STATE_ACTIVE")
        mock_ack.assert_called_once()

    @patch("billing.services.play_purchase_service.get_subscription_purchase")
    @patch("billing.services.play_purchase_service.acknowledge_subscription_purchase")
    def test_mismatched_obfuscated_account_id_is_rejected(self, mock_ack, mock_get):
        """Stops a purchase token observed under a different session from
        being replayed to credit this account."""
        mock_get.return_value = _verified_purchase(obfuscated_account_id="someone-elses-hash")

        res = self.client.post(PLAY_VERIFY_URL, {"purchase_token": "tok_stolen", "product_id": "silvora_pro"})

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(PlayBillingSubscription.objects.filter(purchase_token="tok_stolen").exists())
        mock_ack.assert_not_called()

    @patch("billing.services.play_purchase_service.get_subscription_purchase")
    def test_unconfigured_plan_shows_clean_error(self, mock_get):
        mock_get.return_value = _verified_purchase(
            product_id="silvora_ultra_deluxe",  # no PlayBillingPlan row for this
            obfuscated_account_id=make_obfuscated_account_id(self.user),
        )

        res = self.client.post(PLAY_VERIFY_URL, {"purchase_token": "tok2", "product_id": "silvora_ultra_deluxe"})

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("billing.services.play_purchase_service.get_subscription_purchase")
    def test_google_api_failure_returns_502_not_a_500(self, mock_get):
        fake_resp = type("R", (), {"status": 500, "reason": "Internal Server Error"})()
        mock_get.side_effect = HttpError(resp=fake_resp, content=b"boom")

        res = self.client.post(PLAY_VERIFY_URL, {"purchase_token": "tok3", "product_id": "silvora_pro"})

        self.assertEqual(res.status_code, status.HTTP_502_BAD_GATEWAY)

    @patch("billing.services.play_purchase_service.get_subscription_purchase")
    @patch("billing.services.play_purchase_service.acknowledge_subscription_purchase")
    def test_replaying_the_same_token_is_idempotent(self, mock_ack, mock_get):
        """The app may retry this call after a network blip before
        completePurchase() -- a second call with the same token must not
        re-verify against Google or grant anything a second time."""
        mock_get.return_value = _verified_purchase(
            obfuscated_account_id=make_obfuscated_account_id(self.user)
        )

        res1 = self.client.post(PLAY_VERIFY_URL, {"purchase_token": "tok_retry", "product_id": "silvora_pro"})
        res2 = self.client.post(PLAY_VERIFY_URL, {"purchase_token": "tok_retry", "product_id": "silvora_pro"})

        self.assertEqual(res1.status_code, status.HTTP_200_OK)
        self.assertEqual(res2.status_code, status.HTTP_200_OK)
        mock_get.assert_called_once()
        self.assertEqual(PlayBillingSubscription.objects.filter(purchase_token="tok_retry").count(), 1)


@override_settings(
    GOOGLE_PLAY_PACKAGE_NAME=PACKAGE_NAME,
    PUBSUB_PUSH_SERVICE_ACCOUNT_EMAIL="pubsub-push@silvora-project.iam.gserviceaccount.com",
    PUBSUB_PUSH_AUDIENCE="https://silvora.cloud/api/billing/play/rtdn/",
)
class PlayRTDNWebhookTests(APITestCase):
    def setUp(self):
        cache.clear()
        mail.outbox = []
        self.user = User.objects.create_user(username="rtdnuser@example.com", email="rtdnuser@example.com", password=PW)
        self.plan = PlayBillingPlan.objects.create(
            tier=SubscriptionTier.PRO, interval=RazorpayPlan.Interval.MONTHLY,
            play_product_id="silvora_pro", play_base_plan_id="pro-monthly",
        )
        self.subscription = PlayBillingSubscription.objects.create(
            user=self.user, plan=self.plan, purchase_token="tok_rtdn",
            status="SUBSCRIPTION_STATE_ACTIVE",
        )
        self.quota = UserQuota.objects.create(user=self.user, tier=SubscriptionTier.PRO, limit_bytes=100 * 1024 * 1024 * 1024)

    def _valid_claims(self):
        return {"email_verified": True, "email": "pubsub-push@silvora-project.iam.gserviceaccount.com"}

    def _post(self, body, claims="valid"):
        with patch("billing.views.google_id_token.verify_oauth2_token") as mock_verify:
            if claims == "valid":
                mock_verify.return_value = self._valid_claims()
            elif claims == "bad_email":
                mock_verify.return_value = {"email_verified": True, "email": "attacker@evil.example"}
            elif claims == "invalid":
                mock_verify.side_effect = ValueError("bad token")
            return self.client.post(
                RTDN_URL, data=body, content_type="application/json",
                HTTP_AUTHORIZATION="Bearer sometoken",
            )

    def test_missing_bearer_token_rejected(self):
        res = self.client.post(RTDN_URL, data=b"{}", content_type="application/json")
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_invalid_oidc_token_rejected(self):
        res = self._post(_rtdn_envelope(2, "tok_rtdn"), claims="invalid")
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_wrong_service_account_rejected(self):
        """Binds trust to the SPECIFIC configured service account, not just
        any Google-signed token."""
        res = self._post(_rtdn_envelope(2, "tok_rtdn"), claims="bad_email")
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_notification_returns_ok_and_does_nothing(self):
        envelope = json.dumps({"message": {"data": base64.b64encode(json.dumps({"testNotification": {"version": "1.0"}}).encode()).decode()}}).encode()
        res = self._post(envelope)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_malformed_envelope_returns_200_not_500(self):
        res = self._post(b"not even json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    @patch("billing.views.get_subscription_purchase")
    def test_unknown_purchase_token_does_not_crash(self, mock_get):
        res = self._post(_rtdn_envelope(2, "tok_never_seen"))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        mock_get.assert_not_called()  # no local row to re-verify against

    @patch("billing.views.get_subscription_purchase")
    def test_renewed_updates_period_end_without_changing_tier(self, mock_get):
        mock_get.return_value = _verified_purchase(state="SUBSCRIPTION_STATE_ACTIVE", expiry_time="2027-06-01T00:00:00Z")

        res = self._post(_rtdn_envelope(2, "tok_rtdn"))  # SUBSCRIPTION_RENEWED

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, "SUBSCRIPTION_STATE_ACTIVE")
        self.quota.refresh_from_db()
        self.assertEqual(self.quota.tier, SubscriptionTier.PRO)

    @patch("billing.views.get_subscription_purchase")
    def test_in_grace_period_does_not_downgrade(self, mock_get):
        """Google's own dunning window -- distinct from Silvora's post-
        cancellation grace. Access stays on while Google keeps retrying."""
        mock_get.return_value = _verified_purchase(state="SUBSCRIPTION_STATE_IN_GRACE_PERIOD")

        res = self._post(_rtdn_envelope(6, "tok_rtdn"))  # SUBSCRIPTION_IN_GRACE_PERIOD

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.quota.refresh_from_db()
        self.assertEqual(self.quota.tier, SubscriptionTier.PRO)  # not downgraded
        self.assertEqual(len(mail.outbox), 1)

    @patch("billing.views.get_subscription_purchase")
    def test_on_hold_downgrades_immediately_without_extra_grace(self, mock_get):
        """Google already gave its own grace via IN_GRACE_PERIOD -- ON_HOLD
        means that's exhausted, so this downgrades right away with only a
        purge timer, no additional 7-day grace_ends_at on top."""
        mock_get.return_value = _verified_purchase(state="SUBSCRIPTION_STATE_ON_HOLD")

        res = self._post(_rtdn_envelope(5, "tok_rtdn"))  # SUBSCRIPTION_ON_HOLD

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.quota.refresh_from_db()
        self.assertEqual(self.quota.tier, SubscriptionTier.FREE)
        self.subscription.refresh_from_db()
        self.assertIsNone(self.subscription.grace_ends_at)
        self.assertIsNotNone(self.subscription.purge_at)

    @patch("billing.views.get_subscription_purchase")
    def test_canceled_keeps_access_until_expiry(self, mock_get):
        """Auto-renew off is not the same as access off -- CANCELED must NOT
        downgrade or start grace timers, unlike Razorpay's `cancelled`."""
        mock_get.return_value = _verified_purchase(state="SUBSCRIPTION_STATE_CANCELED")

        res = self._post(_rtdn_envelope(3, "tok_rtdn"))  # SUBSCRIPTION_CANCELED

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.quota.refresh_from_db()
        self.assertEqual(self.quota.tier, SubscriptionTier.PRO)  # still entitled
        self.subscription.refresh_from_db()
        self.assertIsNone(self.subscription.grace_ends_at)
        self.assertIsNone(self.subscription.purge_at)

    @patch("billing.views.get_subscription_purchase")
    def test_expired_starts_grace_and_purge_timers(self, mock_get):
        """This is Play's equivalent of Razorpay's cancelled/completed --
        entitlement is genuinely over now."""
        mock_get.return_value = _verified_purchase(state="SUBSCRIPTION_STATE_EXPIRED")

        res = self._post(_rtdn_envelope(13, "tok_rtdn"))  # SUBSCRIPTION_EXPIRED

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.subscription.refresh_from_db()
        self.assertIsNotNone(self.subscription.grace_ends_at)
        self.assertIsNotNone(self.subscription.purge_at)
        self.assertGreater(self.subscription.purge_at, self.subscription.grace_ends_at)
        self.assertEqual(len(mail.outbox), 1)

    @patch("billing.views.get_subscription_purchase")
    def test_replaying_expired_is_idempotent(self, mock_get):
        mock_get.return_value = _verified_purchase(state="SUBSCRIPTION_STATE_EXPIRED")

        self._post(_rtdn_envelope(13, "tok_rtdn"))
        self.subscription.refresh_from_db()
        first_grace = self.subscription.grace_ends_at
        mail.outbox = []

        res = self._post(_rtdn_envelope(13, "tok_rtdn"))

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.grace_ends_at, first_grace)  # not pushed further out
        self.assertEqual(len(mail.outbox), 0)  # no duplicate email

    @patch("billing.views.get_subscription_purchase")
    def test_revoked_downgrades_without_soft_grace(self, mock_get):
        """A refund/chargeback is a reversal, not a natural lapse -- skips
        the 7-day soft grace that a normal expiry gets."""
        mock_get.return_value = _verified_purchase(state="SUBSCRIPTION_STATE_REVOKED")

        res = self._post(_rtdn_envelope(12, "tok_rtdn"))  # SUBSCRIPTION_REVOKED

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.quota.refresh_from_db()
        self.assertEqual(self.quota.tier, SubscriptionTier.FREE)
        self.subscription.refresh_from_db()
        self.assertIsNone(self.subscription.grace_ends_at)
        self.assertIsNotNone(self.subscription.purge_at)


class CrossProviderTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="crossuser@example.com", email="crossuser@example.com", password=PW)
        self.razorpay_plan = RazorpayPlan.objects.create(
            tier=SubscriptionTier.PRO, interval=RazorpayPlan.Interval.MONTHLY,
            razorpay_plan_id="plan_cross", amount_paise=19900,
        )
        self.play_plan = PlayBillingPlan.objects.create(
            tier=SubscriptionTier.ENTERPRISE, interval=RazorpayPlan.Interval.MONTHLY,
            play_product_id="silvora_enterprise", play_base_plan_id="enterprise-monthly",
        )

    def test_has_active_subscription_true_via_play_alone(self):
        from .services.cross_provider import has_active_subscription
        self.assertFalse(has_active_subscription(self.user))

        PlayBillingSubscription.objects.create(
            user=self.user, plan=self.play_plan, purchase_token="tok_a",
            status="SUBSCRIPTION_STATE_ACTIVE",
        )
        self.assertTrue(has_active_subscription(self.user))

    def test_get_live_subscription_prefers_razorpay_then_falls_back_to_play(self):
        from .services.cross_provider import get_live_subscription
        self.assertIsNone(get_live_subscription(self.user))

        play_sub = PlayBillingSubscription.objects.create(
            user=self.user, plan=self.play_plan, purchase_token="tok_b",
            status="SUBSCRIPTION_STATE_ACTIVE",
        )
        live = get_live_subscription(self.user)
        self.assertEqual(live.provider, "play")
        self.assertEqual(live.subscription, play_sub)

        razorpay_sub = Subscription.objects.create(
            user=self.user, plan=self.razorpay_plan, razorpay_subscription_id="sub_cross", status="active",
        )
        live = get_live_subscription(self.user)
        self.assertEqual(live.provider, "razorpay")
        self.assertEqual(live.subscription, razorpay_sub)

    @patch("billing.services.subscription_service.create_subscription")
    def test_active_play_subscription_blocks_a_new_razorpay_mandate(self, mock_create):
        """Without this guard, a user with an active Play subscription could
        open the website checkout and mint a second, real, paying mandate
        through the other provider."""
        from .services.subscription_service import AlreadySubscribed, create_user_subscription
        PlayBillingSubscription.objects.create(
            user=self.user, plan=self.play_plan, purchase_token="tok_c",
            status="SUBSCRIPTION_STATE_ACTIVE",
        )

        with self.assertRaises(AlreadySubscribed):
            create_user_subscription(self.user, "pro", "monthly")
        mock_create.assert_not_called()
