# users/views.py
import logging

from django.core.signing import BadSignature, SignatureExpired
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from rest_framework_simplejwt.views import TokenObtainPairView

from .models import User
from .serializers import RegisterSerializer
from .services import send_verification_email, unsign_verification_token

logger = logging.getLogger("silvora.users")


class ThrottledTokenObtainPairView(TokenObtainPairView):
    """Login endpoint, rate-limited via the 'login' scope to blunt brute force."""
    throttle_scope = "login"


class RegisterView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_scope = "register"

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        # Best-effort — a failed send must never fail registration. The
        # account already works fully without a verified email (recovery is
        # phrase-based, not email-based), so this is a courtesy, not a gate.
        send_verification_email(user)

        return Response(
            {
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "status": "registered",
            },
            status=status.HTTP_201_CREATED,
        )


class VerifyEmailView(APIView):
    """Public — the token itself is the credential. Non-blocking design:
    this just flips a flag, it never gates login or vault access."""
    permission_classes = [permissions.AllowAny]

    def get(self, request, token):
        try:
            user_id = unsign_verification_token(token)
        except SignatureExpired:
            return Response(
                {"error": "This verification link has expired. Request a new one from the app."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except BadSignature:
            return Response({"error": "Invalid verification link."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response({"error": "Invalid verification link."}, status=status.HTTP_400_BAD_REQUEST)

        if not user.email_verified:
            user.email_verified = True
            user.email_verified_at = timezone.now()
            user.save(update_fields=["email_verified", "email_verified_at"])

        return Response({"status": "email_verified"})


class MeView(APIView):
    """Identity-only profile info for the app to render after login/unlock
    (email-verification banner). Tier/quota deliberately stays on the
    existing /quota/ endpoint rather than duplicating it here."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        return Response({
            "email": request.user.email,
            "email_verified": request.user.email_verified,
        })


class ResendVerificationEmailView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = "email_verify"

    def post(self, request):
        if request.user.email_verified:
            return Response({"status": "already_verified"})

        sent = send_verification_email(request.user)
        if not sent:
            # Don't leak whether it's an SMTP outage vs something else — just
            # tell the user to try again shortly. send_verification_email
            # already logged the real error.
            return Response(
                {"error": "Could not send verification email right now. Please try again shortly."},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response({"status": "verification_sent"})
# ============================================================


