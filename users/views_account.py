# users/views_account.py
from django.db import transaction
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status

from .serializers import DeleteAccountSerializer
from files.services.storage_gateway import StorageGateway


class DeleteAccountView(APIView):
    """Permanently deletes the current user's account: every encrypted file
    blob in storage, all database records (files, master key envelope,
    quota), and the account itself. Requires the account password as a final
    confirmation. Irreversible."""
    permission_classes = [IsAuthenticated]
    throttle_scope = "master_key"

    def post(self, request):
        serializer = DeleteAccountSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user
        if not user.check_password(serializer.validated_data["password"]):
            return Response({"error": "Incorrect password."}, status=status.HTTP_403_FORBIDDEN)

        prefix = f"Silvora/tenants/{user.tenant_id}/users/{user.id}"
        StorageGateway().delete_recursive(prefix)

        with transaction.atomic():
            user.delete()

        return Response({"status": "account_deleted"})
