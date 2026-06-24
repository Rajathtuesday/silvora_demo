# billing/services/razorpay_client.py
"""
Razorpay Subscriptions integration — Silvora's OWN account, collecting
subscription revenue directly. This is a fundamentally different
arrangement (and a different Razorpay product) from a restaurant POS's
bring-your-own-keys one-time QR payments elsewhere — recurring billing needs
the Subscriptions API (recurring mandates: UPI Autopay / cards), not a
one-off QR scan.

Uses `requests` directly, no official `razorpay` SDK — the SDK's only real
value-add is a one-line HMAC check this module already replicates, the same
reasoning used for the equivalent QR-payment integration.
"""
import hmac
import hashlib
import logging

import requests
from django.conf import settings

logger = logging.getLogger("silvora.billing")

RAZORPAY_API_BASE = "https://api.razorpay.com/v1"


def create_subscription(plan_id: str, customer_notify: bool = True) -> dict:
    """
    Creates a Razorpay subscription for the given plan. Returns the raw
    Razorpay response dict (includes `id`, the razorpay_subscription_id the
    Flutter app hands to Razorpay's checkout SDK to complete the mandate).

    Raises requests.RequestException on network/API failure — callers catch
    and surface a clean error.
    """
    response = requests.post(
        f"{RAZORPAY_API_BASE}/subscriptions",
        auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET),
        json={
            "plan_id": plan_id,
            "customer_notify": 1 if customer_notify else 0,
            # total_count: Razorpay requires a finite count for most plan
            # intervals. 120 months (10 years monthly) / 10 years yearly is
            # effectively "until cancelled" for a human subscriber.
            "total_count": 120,
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def verify_webhook_signature(body: bytes, signature_header: str, webhook_secret: str) -> bool:
    """
    Identical HMAC pattern used for Rasova's Razorpay QR webhook and the
    aggregator webhook — kept consistent across both projects deliberately.
    """
    if not signature_header or not webhook_secret:
        return False
    expected_sig = hmac.new(
        webhook_secret.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected_sig, signature_header)
