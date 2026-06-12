# users/views.py
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from rest_framework_simplejwt.views import TokenObtainPairView

from .serializers import RegisterSerializer


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


