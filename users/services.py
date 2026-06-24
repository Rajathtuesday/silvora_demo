# users/services.py
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.core.signing import TimestampSigner

logger = logging.getLogger("silvora.users")

EMAIL_VERIFY_SALT = "email-verify"
EMAIL_VERIFY_MAX_AGE = 60 * 60 * 48  # 48 hours


def make_verification_token(user) -> str:
    return TimestampSigner(salt=EMAIL_VERIFY_SALT).sign(str(user.id))


def unsign_verification_token(token: str) -> str:
    """Returns the user id (str). Raises SignatureExpired / BadSignature on
    an invalid/expired token — callers handle those, not this function."""
    return TimestampSigner(salt=EMAIL_VERIFY_SALT).unsign(token, max_age=EMAIL_VERIFY_MAX_AGE)


def send_verification_email(user) -> bool:
    """
    Best-effort send. Verification is non-blocking by design (recovery
    already works without email), so a failed send must never raise out of
    here — callers (register, resend) just log and move on either way.
    """
    token = make_verification_token(user)
    verify_url = f"{settings.SITE_BASE_URL}/api/auth/verify-email/{token}/"
    try:
        send_mail(
            subject="Verify your Silvora email",
            message=(
                "Welcome to Silvora.\n\n"
                f"Verify your email address:\n{verify_url}\n\n"
                "This link expires in 48 hours. Your account already works "
                "without verifying — this just confirms we can reach you."
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
        )
        return True
    except Exception as e:
        logger.error("Failed to send verification email to user %s: %s", user.id, e)
        return False
