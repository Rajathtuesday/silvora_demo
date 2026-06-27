# billing/services/web_link.py
from django.core.signing import TimestampSigner

# Short-lived on purpose -- this token's only job is to get someone from a
# tap in the app to a logged-in checkout page on the website within the
# same sitting. Same TimestampSigner pattern as email verification
# (users/services.py), different salt so the two token types can never be
# swapped for each other.
BILLING_WEB_SALT = "billing-web"
BILLING_WEB_MAX_AGE = 60 * 10  # 10 minutes


def make_billing_web_token(user) -> str:
    return TimestampSigner(salt=BILLING_WEB_SALT).sign(str(user.id))


def unsign_billing_web_token(token: str) -> str:
    """Returns the user id (str). Raises SignatureExpired / BadSignature on
    an invalid/expired token -- callers handle those, not this function."""
    return TimestampSigner(salt=BILLING_WEB_SALT).unsign(token, max_age=BILLING_WEB_MAX_AGE)
