# files/services/upload_service.py

from datetime import timedelta
from django.utils import timezone
from django.shortcuts import get_object_or_404
from django.db import transaction
from django.core.exceptions import ValidationError

from ..models import FileRecord
from .storage_gateway import StorageGateway
from .quota_service import QuotaService


# ============================================================
# R2 BASE PATH BUILDER
# ============================================================

def r2_base(tenant_id, user_id, file_id):
    return f"Silvora/tenants/{tenant_id}/users/{user_id}/files/{file_id}"


# ============================================================
# UPLOAD SERVICE (ZERO KNOWLEDGE SAFE)
# ============================================================

class UploadService:

    def __init__(self, user):
        self.user = user
        self.storage = StorageGateway()

    # ========================================================
    # START UPLOAD
    # ========================================================

    def start(self, data):

        # 🔐 Encrypted filename inputs
        filename_cipher = data.get("filename_ciphertext")
        filename_nonce = data.get("filename_nonce")
        filename_mac = data.get("filename_mac")

        total_size = int(data.get("size", 0))
        security_mode = data.get("security_mode")

        if (
            not filename_cipher
            or not filename_nonce
            or not filename_mac
            or total_size <= 0
        ):
            return {"error": "Invalid input"}, 400

        # Validate tenant binding
        if not self.user.tenant:
            return {"error": "User not assigned to tenant"}, 400

        # Ensure quotas exist
        user_quota = QuotaService.get_or_create_user_quota(self.user)
        QuotaService.get_or_create_tenant_quota(self.user.tenant)

        if not user_quota.can_store(total_size):
            return {"error": "Quota exceeded"}, 403

        expires_at = timezone.now() + timedelta(hours=24)

        # Create file record (server-blind)
        file = FileRecord.objects.create(
            owner=self.user,
            tenant=self.user.tenant,
            filename_ciphertext=bytes.fromhex(filename_cipher),
            filename_nonce=bytes.fromhex(filename_nonce),
            filename_mac=bytes.fromhex(filename_mac),
            security_mode=security_mode,
            upload_state=FileRecord.UploadState.INITIATED,
            upload_expires_at=expires_at,
        )

        return {
            "file_id": str(file.id),
            "upload_state": file.upload_state,
            "expires_at": expires_at.isoformat(),
        }, 200

    # ========================================================
    # RESUME UPLOAD
    # ========================================================

    def resume(self, file_id):

        file = get_object_or_404(
            FileRecord,
            id=file_id,
            owner=self.user,
            tenant=self.user.tenant,
        )

        base = r2_base(
            file.tenant_id,
            file.owner_id,
            file.id,
        )

        chunk_indices = self.storage.list_chunks(base)

        return {
            "file_id": str(file.id),
            "upload_state": file.upload_state,
            "uploaded_indices": chunk_indices,
        }, 200

    # ========================================================
    # UPLOAD CHUNK
    # ========================================================

    def upload_chunk(self, file_id, index, data):

        file = get_object_or_404(
            FileRecord,
            id=file_id,
            owner=self.user,
            tenant=self.user.tenant,
        )

        # Expiry protection
        if file.upload_expires_at and timezone.now() > file.upload_expires_at:
            file.upload_state = FileRecord.UploadState.FAILED
            file.save(update_fields=["upload_state"])
            return {"error": "Upload expired"}, 400

        if file.upload_state not in [
            FileRecord.UploadState.INITIATED,
            FileRecord.UploadState.UPLOADING,
        ]:
            return {"error": "Invalid upload state"}, 400

        base = r2_base(
            file.tenant_id,
            file.owner_id,
            file.id,
        )

        self.storage.upload_bytes(
            data,
            f"{base}/chunks/chunk_{index}.bin"
        )

        if file.upload_state == FileRecord.UploadState.INITIATED:
            file.upload_state = FileRecord.UploadState.UPLOADING
            file.save(update_fields=["upload_state"])

        return {"stored": True}, 200

    # ========================================================
    # COMMIT UPLOAD
    # ========================================================

    @transaction.atomic
    def commit(self, file_id):

        file = get_object_or_404(
            FileRecord.objects.select_for_update(),
            id=file_id,
            owner=self.user,
            tenant=self.user.tenant,
        )

        if file.upload_state == FileRecord.UploadState.COMMITTED:
            return {"status": "already_committed"}, 200

        if file.upload_expires_at and timezone.now() > file.upload_expires_at:
            file.upload_state = FileRecord.UploadState.FAILED
            file.save(update_fields=["upload_state"])
            return {"error": "Upload expired"}, 400

        if file.upload_state not in [
            FileRecord.UploadState.UPLOADING,
            FileRecord.UploadState.COMPLETED,
        ]:
            return {"error": "Invalid state for commit"}, 400

        base = r2_base(
            file.tenant_id,
            file.owner_id,
            file.id,
        )

        total_size = self.storage.calculate_total_chunk_size(base)

        success = QuotaService.consume(self.user, total_size)

        if not success:
            return {"error": "Quota exceeded"}, 403

        file.size = total_size
        file.upload_state = FileRecord.UploadState.COMMITTED
        file.final_path = base
        file.save(update_fields=["size", "upload_state", "final_path"])

        return {"status": "committed"}, 200