# billing/services/obfuscated_account.py
"""
What stops a replayed Play purchase token from being credited to the wrong
account. Play Billing Library accepts an `obfuscatedAccountId` at purchase
time (via GooglePlayPurchaseParam.applicationUserName on the Flutter side)
and echoes it back verifiably in the server-side purchase response
(externalAccountIdentifiers.obfuscatedExternalAccountId). The verify step
recomputes this deterministically and compares -- so a token observed/stolen
from one session can't be replayed to credit a different account, without
needing to store anything extra.

Deterministic, not stored -- same "server mints an opaque derived token,
client just carries it" shape as web_link.py, but this one never needs to be
looked up later, only recomputed and compared.
"""
import hashlib
import hmac

from django.conf import settings

# Play's obfuscatedAccountId has a 64-character limit; a hex SHA-256 digest
# is exactly 64 characters, so no truncation is needed.
OBFUSCATED_ACCOUNT_SALT = b"play-obfuscated-account"


def make_obfuscated_account_id(user) -> str:
    return hmac.new(
        settings.SECRET_KEY.encode(),
        OBFUSCATED_ACCOUNT_SALT + str(user.id).encode(),
        hashlib.sha256,
    ).hexdigest()
