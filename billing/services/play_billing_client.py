# billing/services/play_billing_client.py
"""
Google Play Billing integration -- Silvora's own Android Publisher API
access, used to verify a purchase token server-side (never trust a
client-reported token alone) and to acknowledge a completed purchase.

Uses the official googleapiclient discovery client, unlike razorpay_client.py's
direct `requests` calls -- there's no equivalent lightweight REST surface for
the Android Publisher API worth reimplementing by hand; the discovery client
is the standard, Google-maintained way to call it.
"""
import json
import logging

from django.conf import settings
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger("silvora.billing")

ANDROID_PUBLISHER_SCOPES = ["https://www.googleapis.com/auth/androidpublisher"]

_client = None  # module-level cache -- credentials self-refresh, no need to rebuild per request


def _get_client():
    global _client
    if _client is not None:
        return _client

    if settings.GOOGLE_PLAY_SERVICE_ACCOUNT_JSON:
        info = json.loads(settings.GOOGLE_PLAY_SERVICE_ACCOUNT_JSON)
        credentials = service_account.Credentials.from_service_account_info(
            info, scopes=ANDROID_PUBLISHER_SCOPES
        )
    elif settings.GOOGLE_PLAY_SERVICE_ACCOUNT_JSON_PATH:
        credentials = service_account.Credentials.from_service_account_file(
            settings.GOOGLE_PLAY_SERVICE_ACCOUNT_JSON_PATH, scopes=ANDROID_PUBLISHER_SCOPES
        )
    else:
        raise RuntimeError(
            "Neither GOOGLE_PLAY_SERVICE_ACCOUNT_JSON nor "
            "GOOGLE_PLAY_SERVICE_ACCOUNT_JSON_PATH is set."
        )

    _client = build("androidpublisher", "v3", credentials=credentials, cache_discovery=False)
    return _client


def get_subscription_purchase(package_name: str, purchase_token: str) -> dict:
    """
    Calls purchases.subscriptionsv2.get. This call IS the verification step --
    an unrecognized, expired, or wrong-package token raises HttpError here,
    which callers treat as "reject this purchase," never as a soft failure.

    Returns the raw SubscriptionPurchaseV2 dict (subscriptionState,
    lineItems[].productId, lineItems[].offerDetails.basePlanId,
    externalAccountIdentifiers.obfuscatedExternalAccountId, etc).
    """
    client = _get_client()
    request = client.purchases().subscriptionsv2().get(
        packageName=package_name,
        token=purchase_token,
    )
    return request.execute()


def acknowledge_subscription_purchase(package_name: str, purchase_token: str) -> None:
    """
    Play auto-refunds any purchase left unacknowledged for 3 days. Best-effort
    on purpose -- the tier grant already happened locally by the time this is
    called, so a failed ack here should not undo it, only get logged.
    """
    try:
        client = _get_client()
        client.purchases().subscriptions().acknowledge(
            packageName=package_name,
            subscriptionId="",  # deprecated/ignored on the acknowledge call, token is what matters
            token=purchase_token,
            body={},
        ).execute()
    except HttpError as e:
        # Status 400 here commonly means "already acknowledged" (e.g. a
        # retried verify call) -- not a real failure, nothing to log loudly for.
        if e.resp.status != 400:
            logger.error("Failed to acknowledge Play purchase token=%s: %s", purchase_token, e)
    except Exception as e:
        logger.error("Failed to acknowledge Play purchase token=%s: %s", purchase_token, e)
