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

class GetMasterKeyMetaView(APIView):
    """
    Returns the encrypted master key envelope for the current user.
    The client uses this to decrypt the vault locally.
    """
    permission_classes = [IsAuthenticated]
    throttle_scope = "master_key"

    def get(self, request):
        envelope = get_object_or_404(
            MasterKeyEnvelope,
            user=request.user,
        )
        serializer = MasterKeyMetaSerializer(envelope)
        return Response(serializer.data)

class SetupMasterKeyView(APIView):
    """
    Stores a new encrypted master key envelope created by the client.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if MasterKeyEnvelope.objects.filter(user=request.user).exists():
            return Response(
                {"error": "Master key already exists for this vault."},
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