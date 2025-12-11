# users/views.py
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from .serializers import RegisterSerializer
# from .models import UserProfile

class RegisterView(APIView):
    permission_classes = [permissions.AllowAny]
    def post(self, request):
        s = RegisterSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        user = s.save()
        return Response({"username": user.username, "id": user.id}, status=status.HTTP_201_CREATED)


class MasterKeyView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        profile = request.user.profile
        emk = profile.encrypted_master_key
        import base64
        return Response({
            "encrypted_master_key_hex": emk.hex() if emk else None,
            "encrypted_master_key_b64": base64.b64encode(emk).decode("ascii") if emk else None,
            "enc_algo": profile.enc_algo,
            "key_salt_b64": profile.key_salt_b64,
            "nonce_b64": profile.nonce_b64,
        })

    def put(self, request):
        profile = request.user.profile
        data = request.data
        import base64

        enc_hex = data.get("encrypted_master_key_hex")
        enc_b64 = data.get("encrypted_master_key_b64")

        if enc_hex:
            try:
                profile.encrypted_master_key = bytes.fromhex(enc_hex)
            except Exception:
                return Response({"detail": "invalid hex"}, status=400)
        elif enc_b64:
            try:
                profile.encrypted_master_key = base64.b64decode(enc_b64)
            except Exception:
                return Response({"detail": "invalid base64"}, status=400)

        profile.enc_algo = data.get("enc_algo", profile.enc_algo)
        profile.key_salt_b64 = data.get("key_salt_b64", profile.key_salt_b64)
        profile.nonce_b64 = data.get("nonce_b64", profile.nonce_b64)
        profile.save()
        return Response({"status": "stored"})
