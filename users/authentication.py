# users/authentication.py
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.exceptions import AuthenticationFailed
from .models import Device


class DeviceJWTAuthentication(JWTAuthentication):

    def get_user(self, validated_token):
        user = super().get_user(validated_token)

        device_id = validated_token.get("device_id")

        if not device_id:
            raise AuthenticationFailed("Device binding missing")

        try:
            device = Device.objects.get(
                id=device_id,
                user=user,
                is_active=True
            )
        except Device.DoesNotExist:
            raise AuthenticationFailed("Device revoked")

        return user