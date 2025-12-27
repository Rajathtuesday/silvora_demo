# # # users/views.py
# # from rest_framework.views import APIView
# # from rest_framework.response import Response
# # from rest_framework import status, permissions
# # from .serializers import RegisterSerializer
# # # from .models import UserProfile

# # class RegisterView(APIView):
# #     permission_classes = [permissions.AllowAny]
# #     def post(self, request):
# #         s = RegisterSerializer(data=request.data)
# #         s.is_valid(raise_exception=True)
# #         user = s.save()
# #         return Response({"username": user.username, "id": user.id}, status=status.HTTP_201_CREATED)


# # class MasterKeyView(APIView):
# #     permission_classes = [permissions.IsAuthenticated]

# #     def get(self, request):
# #         profile = request.user.profile
# #         emk = profile.encrypted_master_key
# #         import base64
# #         return Response({
# #             "encrypted_master_key_hex": emk.hex() if emk else None,
# #             "encrypted_master_key_b64": base64.b64encode(emk).decode("ascii") if emk else None,
# #             "enc_algo": profile.enc_algo,
# #             "key_salt_b64": profile.key_salt_b64,
# #             "nonce_b64": profile.nonce_b64,
# #         })

# #     def put(self, request):
# #         profile = request.user.profile
# #         data = request.data
# #         import base64

# #         enc_hex = data.get("encrypted_master_key_hex")
# #         enc_b64 = data.get("encrypted_master_key_b64")

# #         if enc_hex:
# #             try:
# #                 profile.encrypted_master_key = bytes.fromhex(enc_hex)
# #             except Exception:
# #                 return Response({"detail": "invalid hex"}, status=400)
# #         elif enc_b64:
# #             try:
# #                 profile.encrypted_master_key = base64.b64decode(enc_b64)
# #             except Exception:
# #                 return Response({"detail": "invalid base64"}, status=400)

# #         profile.enc_algo = data.get("enc_algo", profile.enc_algo)
# #         profile.key_salt_b64 = data.get("key_salt_b64", profile.key_salt_b64)
# #         profile.nonce_b64 = data.get("nonce_b64", profile.nonce_b64)
# #         profile.save()
# #         return Response({"status": "stored"})

# # users/views.py

# from rest_framework.views import APIView
# from rest_framework.response import Response
# from rest_framework import status, permissions
# from django.contrib.auth import get_user_model
# from django.db import transaction
# from django.utils import timezone

# from .serializers import RegisterSerializer
# from .models import MasterKey

# import base64

# User = get_user_model()


# # ============================================================
# # REGISTER
# # ============================================================

# class RegisterView(APIView):
#     """
#     User registration endpoint.

#     Responsibilities:
#     - Create user
#     - Store email (for future verification / marketing)
#     - DOES NOT touch encryption or payments
#     """

#     permission_classes = [permissions.AllowAny]

#     @transaction.atomic
#     def post(self, request):
#         serializer = RegisterSerializer(data=request.data)
#         serializer.is_valid(raise_exception=True)

#         user = serializer.save()

#         # 🔐 Ensure MasterKey row exists (EMPTY at start)
#         MasterKey.objects.get_or_create(
#             user=user,
#             defaults={
#                 "encrypted_master_key_hex": "",
#                 "kdf_salt_b64": "",
#                 "nonce_b64": "",
#             },
#         )

#         return Response(
#             {
#                 "id": user.id,
#                 "username": user.username,
#                 "email": user.email,
#                 "status": "registered",
#             },
#             status=status.HTTP_201_CREATED,
#         )


# # ============================================================
# # MASTER KEY STORAGE (CLIENT → SERVER, ENCRYPTED ONLY)
# # ============================================================

# class MasterKeyView(APIView):
#     """
#     Stores and retrieves the user's encrypted master key.

#     Server NEVER sees plaintext keys.
#     """

#     permission_classes = [permissions.IsAuthenticated]

#     def get(self, request):
#         mk = request.user.master_key

#         if not mk.encrypted_master_key_hex:
#             return Response(
#                 {"encrypted_master_key": None},
#                 status=status.HTTP_200_OK,
#             )

#         return Response(
#             {
#                 "encrypted_master_key_hex": mk.encrypted_master_key_hex,
#                 "kdf_salt_b64": mk.kdf_salt_b64,
#                 "kdf_algorithm": mk.kdf_algorithm,
#                 "kdf_iterations": mk.kdf_iterations,
#                 "aead_algorithm": mk.aead_algorithm,
#                 "nonce_b64": mk.nonce_b64,
#                 "version": mk.version,
#             },
#             status=status.HTTP_200_OK,
#         )

#     def put(self, request):
#         mk = request.user.master_key
#         data = request.data

#         # -------------------------
#         # Required fields
#         # -------------------------
#         enc_hex = data.get("encrypted_master_key_hex")
#         salt_b64 = data.get("kdf_salt_b64")
#         nonce_b64 = data.get("nonce_b64")

#         if not enc_hex or not salt_b64 or not nonce_b64:
#             return Response(
#                 {"detail": "Missing required key fields"},
#                 status=status.HTTP_400_BAD_REQUEST,
#             )

#         # -------------------------
#         # Validate formats
#         # -------------------------
#         try:
#             bytes.fromhex(enc_hex)
#             base64.b64decode(salt_b64)
#             base64.b64decode(nonce_b64)
#         except Exception:
#             return Response(
#                 {"detail": "Invalid encoding"},
#                 status=status.HTTP_400_BAD_REQUEST,
#             )

#         # -------------------------
#         # Persist (opaque to server)
#         # -------------------------
#         mk.encrypted_master_key_hex = enc_hex
#         mk.kdf_salt_b64 = salt_b64
#         mk.nonce_b64 = nonce_b64

#         mk.kdf_algorithm = data.get(
#             "kdf_algorithm", mk.kdf_algorithm
#         )
#         mk.kdf_iterations = data.get(
#             "kdf_iterations", mk.kdf_iterations
#         )
#         mk.aead_algorithm = data.get(
#             "aead_algorithm", mk.aead_algorithm
#         )

#         mk.version += 1
#         mk.updated_at = timezone.now()
#         mk.save()

#         return Response(
#             {"status": "master_key_stored", "version": mk.version},
#             status=status.HTTP_200_OK,
#         )
# # ============================================================

# users/views.py
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions

from .serializers import RegisterSerializer


class RegisterView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        return Response(
            {
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "status": "registered",
            },
            status=status.HTTP_201_CREATED,
        )
# ============================================================


