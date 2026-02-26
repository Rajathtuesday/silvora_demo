# # users/views_masterkey.py

# from rest_framework.decorators import api_view, permission_classes
# from rest_framework.permissions import IsAuthenticated
# from rest_framework.response import Response
# from rest_framework import status

# from .models import UserMasterKey


# @api_view(["POST"])
# @permission_classes([IsAuthenticated])
# def setup_master_key(request):
#     """
#     Create or update the encrypted master key metadata for the current user.

#     Expected JSON body:
#     {
#       "mk_ciphertext_b64": "...",
#       "mk_nonce_b64": "...",
#       "mk_mac_b64": "...",
#       "mk_salt_b64": "...",
#       "mk_algo": "xchacha20-poly1305+pbkdf2-sha256"
#     }
#     """
#     user = request.user
#     data = request.data or {}

#     required_fields = [
#         "mk_ciphertext_b64",
#         "mk_nonce_b64",
#         "mk_mac_b64",
#         "mk_salt_b64",
#         "mk_algo",
#     ]

#     missing = [f for f in required_fields if f not in data or not data[f]]
#     if missing:
#         return Response(
#             {"error": f"Missing fields: {', '.join(missing)}"},
#             status=status.HTTP_400_BAD_REQUEST,
#         )

#     mk_ciphertext_b64 = data["mk_ciphertext_b64"]
#     mk_nonce_b64 = data["mk_nonce_b64"]
#     mk_mac_b64 = data["mk_mac_b64"]
#     mk_salt_b64 = data["mk_salt_b64"]
#     mk_algo = data.get("mk_algo", "xchacha20-poly1305+pbkdf2-sha256")

#     obj, created = UserMasterKey.objects.update_or_create(
#         user=user,
#         defaults={
#             "mk_ciphertext_b64": mk_ciphertext_b64,
#             "mk_nonce_b64": mk_nonce_b64,
#             "mk_mac_b64": mk_mac_b64,
#             "mk_salt_b64": mk_salt_b64,
#             "mk_algo": mk_algo,
#         },
#     )

#     return Response(
#         {
#             "status": 1,
#             "message": "master key stored" if created else "master key updated",
#         },
#         status=status.HTTP_200_OK,
#     )


# @api_view(["GET"])
# @permission_classes([IsAuthenticated])
# def get_master_key_meta(request):
#     """
#     Return the encrypted master key envelope for the current user.

#     Response 200:
#     {
#       "has_master_key": true,
#       "mk_ciphertext_b64": "...",
#       "mk_nonce_b64": "...",
#       "mk_mac_b64": "...",
#       "mk_salt_b64": "...",
#       "mk_algo": "..."
#     }

#     Response 404 if user has no master key yet.
#     """
#     user = request.user

#     try:
#         mk = UserMasterKey.objects.get(user=user)
#     except UserMasterKey.DoesNotExist:
#         return Response(
#             {"has_master_key": False, "error": "no master key for this user"},
#             status=status.HTTP_404_NOT_FOUND,
#         )

#     return Response(
#         {
#             "has_master_key": True,
#             "mk_ciphertext_b64": mk.mk_ciphertext_b64,
#             "mk_nonce_b64": mk.mk_nonce_b64,
#             "mk_mac_b64": mk.mk_mac_b64,
#             "mk_salt_b64": mk.mk_salt_b64,
#             "mk_algo": mk.mk_algo,
#         },
#         status=status.HTTP_200_OK,
#     )



# =------------------------------------------------------------------=

# # users/views_masterkey.py
# from rest_framework.decorators import api_view, permission_classes
# from rest_framework.permissions import IsAuthenticated
# from rest_framework.response import Response
# from rest_framework import status

# from .models import MasterKey


# @api_view(["GET"])
# @permission_classes([IsAuthenticated])
# def get_master_key_meta(request):
#     """
#     Return metadata about the user's master key (but NOT the plaintext).
#     Used by the client to know:
#       - does a master key exist?
#       - which KDF params do I need to use?
#     """
#     user = request.user

#     try:
#         mk = MasterKey.objects.get(user=user)
#     except MasterKey.DoesNotExist:
#         return Response(
#             {
#                 "has_master_key": False,
#             },
#             status=status.HTTP_200_OK,
#         )

#     return Response(
#         {
#             "has_master_key": True,
#             "version": mk.version,
#             "kdf_algorithm": mk.kdf_algorithm,
#             "kdf_iterations": mk.kdf_iterations,
#             "kdf_salt_b64": mk.kdf_salt_b64,
#             "aead_algorithm": mk.aead_algorithm,
#             "nonce_b64": mk.nonce_b64,
#             # Encrypted MK itself (client needs this to decrypt)
#             "encrypted_master_key_hex": mk.encrypted_master_key_hex,
#         },
#         status=status.HTTP_200_OK,
#     )


# @api_view(["POST"])
# @permission_classes([IsAuthenticated])
# def setup_master_key(request):
#     """
#     One-time setup of a user's master key.

#     Client responsibilities:
#       - generate random 32-byte master key
#       - generate random salt
#       - derive KEK from password using PBKDF2/Argon2
#       - encrypt master key with AEAD (XChaCha20-Poly1305)
#       - send ciphertext + KDF params + nonce to this endpoint

#     This endpoint ONLY stores what the client sends, and NEVER decrypts.
#     Later we can extend with 'recovery_blob' for hybrid model C.
#     """
#     user = request.user

#     # Prevent overwriting an existing MK for now (simple rule).
#     if MasterKey.objects.filter(user=user).exists():
#         return Response(
#             {"detail": "Master key already set up for this user."},
#             status=status.HTTP_400_BAD_REQUEST,
#         )

#     data = request.data or {}

#     encrypted_master_key_hex = data.get("encrypted_master_key_hex")
#     kdf_salt_b64 = data.get("kdf_salt_b64")
#     kdf_algorithm = data.get("kdf_algorithm", "pbkdf2-hmac-sha256")
#     kdf_iterations = data.get("kdf_iterations", 150_000)
#     aead_algorithm = data.get("aead_algorithm", "xchacha20-poly1305")
#     nonce_b64 = data.get("nonce_b64")
#     version = data.get("version", 1)

#     # Minimal validation
#     if not encrypted_master_key_hex or not kdf_salt_b64 or not nonce_b64:
#         return Response(
#             {
#                 "detail": "encrypted_master_key_hex, kdf_salt_b64 and nonce_b64 are required."
#             },
#             status=status.HTTP_400_BAD_REQUEST,
#         )

#     mk = MasterKey.objects.create(
#         user=user,
#         encrypted_master_key_hex=encrypted_master_key_hex,
#         kdf_salt_b64=kdf_salt_b64,
#         kdf_algorithm=kdf_algorithm,
#         kdf_iterations=int(kdf_iterations),
#         aead_algorithm=aead_algorithm,
#         nonce_b64=nonce_b64,
#         version=int(version),
#         # recovery_blob stays NULL for now; used later for hybrid recovery.
#     )

#     return Response(
#         {
#             "status": "ok",
#             "message": "Master key stored.",
#             "version": mk.version,
#         },
#         status=status.HTTP_201_CREATED,
#     )



# ===---------------------------------------------------------------===


# # users/views_masterkey.py
# from rest_framework.decorators import api_view, permission_classes
# from rest_framework.permissions import IsAuthenticated
# from rest_framework.response import Response
# from rest_framework import status

# from .models import MasterKey
# from .serializers import MasterKeyMetaSerializer, MasterKeySetupSerializer


# @api_view(["GET"])
# @permission_classes([IsAuthenticated])
# def get_master_key_meta(request):
#     """
#     GET /api/auth/masterkey/meta/

#     Used right after login:
#     - tells client if a master key exists
#     - returns only metadata (no key / no ciphertext)
#     """
#     try:
#         mk = MasterKey.objects.get(user=request.user)
#     except MasterKey.DoesNotExist:
#         # No master key yet
#         return Response(
#             {
#                 "has_master_key": False,
#                 "kdf_algorithm": None,
#                 "kdf_iterations": None,
#                 "aead_algorithm": None,
#                 "version": None,
#             },
#             status=status.HTTP_200_OK,
#         )

#     serializer = MasterKeyMetaSerializer(mk)
#     data = serializer.data
#     # ensure flag present even if serializer changes later
#     data["has_master_key"] = True
#     return Response(data, status=status.HTTP_200_OK)


# @api_view(["POST"])
# @permission_classes([IsAuthenticated])
# def setup_master_key(request):
#     """
#     POST /api/auth/masterkey/setup/

#     Body JSON:
#     {
#       "encrypted_master_key_hex": "...",
#       "kdf_salt_b64": "...",
#       "kdf_algorithm": "pbkdf2-hmac-sha256",
#       "kdf_iterations": 150000,
#       "aead_algorithm": "xchacha20-poly1305",
#       "nonce_b64": "..."
#     }

#     Client already:
#       - generated random master key
#       - derived KEK from password with KDF
#       - encrypted master key with AEAD (XChaCha20-Poly1305)
#     """
#     try:
#         mk = MasterKey.objects.get(user=request.user)
#         # Update existing
#         serializer = MasterKeySetupSerializer(
#             mk, data=request.data, context={"request": request}
#         )
#     except MasterKey.DoesNotExist:
#         # Create new
#         serializer = MasterKeySetupSerializer(
#             data=request.data, context={"request": request}
#         )

#     serializer.is_valid(raise_exception=True)
#     mk_obj = serializer.save()

#     return Response(
#         {
#             "status": 1,
#             "message": "master key stored",
#             "version": mk_obj.version,
#         },
#         status=status.HTTP_200_OK,
#     )
# ============================================================

# # users/views.py
# from rest_framework.views import APIView
# from rest_framework.response import Response
# from rest_framework import status, permissions

# from .serializers import RegisterSerializer


# class RegisterView(APIView):
#     permission_classes = [permissions.AllowAny]

#     def post(self, request):
#         serializer = RegisterSerializer(data=request.data)
#         serializer.is_valid(raise_exception=True)
#         user = serializer.save()

#         return Response(
#             {
#                 "id": user.id,
#                 "username": user.username,
#                 "email": user.email,
#                 "status": "registered",
#             },
#             status=status.HTTP_201_CREATED,
#         )
# ============================================================

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status
from django.shortcuts import get_object_or_404

from .models import MasterKeyEnvelope
from .serializers import (
    MasterKeyMetaSerializer,
    MasterKeySetupSerializer,
)


# ==========================================
# GET MASTER KEY META
# ==========================================

class GetMasterKeyMetaView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):

        envelope = get_object_or_404(
            MasterKeyEnvelope,
            user=request.user,
        )

        serializer = MasterKeyMetaSerializer(envelope)
        return Response(serializer.data)


# ==========================================
# SETUP MASTER KEY
# ==========================================

class SetupMasterKeyView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):

        if MasterKeyEnvelope.objects.filter(user=request.user).exists():
            return Response(
                {"error": "Master key already exists"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = MasterKeySetupSerializer(
            data=request.data,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        return Response(
            {"status": "master_key_created"},
            status=status.HTTP_201_CREATED,
        )