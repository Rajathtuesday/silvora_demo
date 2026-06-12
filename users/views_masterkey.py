from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework import status

from .models import MasterKeyEnvelope
from .serializers import (
    MasterKeyMetaSerializer,
    MasterKeySetupSerializer,
    RecoveryMetaSerializer,
    RecoverSerializer,
    ChangePasswordSerializer,
)

User = get_user_model()


def _apply_new_password_envelope(env, data):
    """Set the password-wrapped envelope fields from validated serializer data."""
    env.enc_master_key = data["enc_master_key"]
    env.enc_master_key_nonce = data["enc_master_key_nonce"]
    env.kdf_salt = data["kdf_salt"]
    env.kdf_memory_kb = data["kdf_memory_kb"]
    env.kdf_iterations = data["kdf_iterations"]
    env.kdf_parallelism = data["kdf_parallelism"]


class GetMasterKeyMetaView(APIView):
    """Returns the encrypted master key envelope for the current user."""
    permission_classes = [IsAuthenticated]
    throttle_scope = "master_key"

    def get(self, request):
        envelope = get_object_or_404(MasterKeyEnvelope, user=request.user)
        return Response(MasterKeyMetaSerializer(envelope).data)


class SetupMasterKeyView(APIView):
    """Stores a new encrypted master key envelope (password + optional recovery)."""
    permission_classes = [IsAuthenticated]
    throttle_scope = "master_key"

    def post(self, request):
        if MasterKeyEnvelope.objects.filter(user=request.user).exists():
            return Response(
                {"error": "Master key already exists for this vault."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = MasterKeySetupSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({"status": "master_key_created"}, status=status.HTTP_201_CREATED)


class ChangePasswordView(APIView):
    """Logged-in password change. The client decrypts the master key with the
    old password and re-wraps it with the new one; we just store the new
    envelope and update the account password."""
    permission_classes = [IsAuthenticated]
    throttle_scope = "master_key"

    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        env = get_object_or_404(MasterKeyEnvelope, user=request.user)
        with transaction.atomic():
            request.user.set_password(data["new_password"])
            request.user.save(update_fields=["password"])
            _apply_new_password_envelope(env, data)
            env.save()
        return Response({"status": "password_changed"})


class RecoveryStartView(APIView):
    """Logged-out: given an email, return the recovery envelope meta so the
    client can derive the Recovery-KEK and decrypt the master key locally."""
    permission_classes = [AllowAny]
    throttle_scope = "login"

    def post(self, request):
        email = (request.data.get("email") or "").strip().lower()
        env = MasterKeyEnvelope.objects.filter(
            user__email=email, enc_master_key_recovery__isnull=False
        ).first()
        if not env:
            return Response({"error": "No recovery available for this account."},
                            status=status.HTTP_404_NOT_FOUND)
        return Response(RecoveryMetaSerializer(env).data)


class RecoverCompleteView(APIView):
    """Logged-out: verify the recovery phrase (via recovery_auth_key hash),
    then reset the account password and store the new password-wrapped
    envelope. Without the real phrase the auth-key can't be produced, so an
    account cannot be taken over by anyone who only knows the email."""
    permission_classes = [AllowAny]
    throttle_scope = "login"

    def post(self, request):
        serializer = RecoverSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        email = data["email"].strip().lower()
        env = (MasterKeyEnvelope.objects
               .select_related("user")
               .filter(user__email=email)
               .first())
        if not env or not env.recovery_auth_hash:
            return Response({"error": "Recovery not available."},
                            status=status.HTTP_404_NOT_FOUND)
        if not check_password(data["recovery_auth_key"], env.recovery_auth_hash):
            return Response({"error": "Invalid recovery phrase."},
                            status=status.HTTP_403_FORBIDDEN)

        with transaction.atomic():
            user = env.user
            user.set_password(data["new_password"])
            user.save(update_fields=["password"])
            _apply_new_password_envelope(env, data)
            env.save()
        return Response({"status": "password_reset"})
