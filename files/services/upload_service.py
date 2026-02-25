# files/services/upload_service.py
from datetime import timedelta
from django.utils import timezone
from django.shortcuts import get_object_or_404
from django.db import transaction

from ..models import FileRecord
from .storage_gateway import StorageGateway
from .quota_service import QuotaService


def r2_base(tenant_id, user_id, file_id):
    return f"Silvora/tenants/{tenant_id}/users/{user_id}/files/{file_id}"


class UploadService:

    def __init__(self, user):
        self.user = user
        self.storage = StorageGateway()

    # ==================================================
    # START
    # ==================================================

    def start(self, data):

        filename_cipher = data.get("filename_ciphertext")
        nonce = data.get("filename_nonce")
        mac = data.get("filename_mac")
        total_size = int(data.get("size", 0))
        security_mode = data.get("security_mode")

        if not filename_cipher or not nonce or not mac:
            return {"error": "Invalid input"}, 400

        quota = QuotaService.get_or_create(self.user)
        if not quota.can_store(total_size):
            return {"error": "Quota exceeded"}, 403

        expires_at = timezone.now() + timedelta(hours=24)

        file = FileRecord.objects.create(
            owner=self.user,
            tenant=self.user.profile.tenant,
            filename_ciphertext=filename_cipher,
            filename_nonce=nonce,
            filename_mac=mac,
            size=0,
            security_mode=security_mode,
            storage_type=FileRecord.STORAGE_R2,
            upload_state=FileRecord.STATE_INITIATED,
            upload_expires_at=expires_at,
        )

        return {
            "file_id": str(file.id),
            "upload_state": file.upload_state,
            "expires_at": expires_at.isoformat(),
        }, 200

    # ==================================================
    # RESUME
    # ==================================================

    def resume(self, file_id):

        file = get_object_or_404(
            FileRecord,
            id=file_id,
            owner=self.user,
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
            "expires_at": (
                file.upload_expires_at.isoformat()
                if file.upload_expires_at else None
            ),
            "uploaded_indices": chunk_indices,
        }, 200

    # ==================================================
    # UPLOAD CHUNK
    # ==================================================

    def upload_chunk(self, file_id, index, data):

        file = get_object_or_404(
            FileRecord,
            id=file_id,
            owner=self.user,
        )

        if file.upload_expires_at and timezone.now() > file.upload_expires_at:
            file.upload_state = FileRecord.STATE_FAILED
            file.save(update_fields=["upload_state"])
            return {"error": "Upload expired"}, 400

        if file.upload_state not in [
            FileRecord.STATE_INITIATED,
            FileRecord.STATE_UPLOADING,
        ]:
            return {"error": "Invalid upload state"}, 400

        base = r2_base(
            file.tenant_id,
            file.owner_id,
            file.id,
        )

        self.storage.upload_chunk(
            data,
            f"{base}/chunks/chunk_{index}.bin"
        )

        if file.upload_state == FileRecord.STATE_INITIATED:
            file.upload_state = FileRecord.STATE_UPLOADING
            file.save(update_fields=["upload_state"])

        return {"stored": True}, 200

    # ==================================================
    # UPLOAD MANIFEST
    # ==================================================

    def upload_manifest(self, file_id, manifest_bytes):

        file = get_object_or_404(
            FileRecord,
            id=file_id,
            owner=self.user,
        )

        if file.upload_expires_at and timezone.now() > file.upload_expires_at:
            file.upload_state = FileRecord.STATE_FAILED
            file.save(update_fields=["upload_state"])
            return {"error": "Upload expired"}, 400

        if file.upload_state != FileRecord.STATE_UPLOADING:
            return {"error": "Invalid state for manifest upload"}, 400

        base = r2_base(
            file.tenant_id,
            file.owner_id,
            file.id,
        )

        manifest_key = f"{base}/manifest.enc"

        self.storage.upload_manifest_blob(
            manifest_bytes,
            manifest_key,
        )

        file.upload_state = FileRecord.STATE_COMPLETED
        file.manifest_path = manifest_key
        file.save(update_fields=["upload_state", "manifest_path"])

        return {"manifest_stored": True}, 200

    # ==================================================
    # COMMIT
    # ==================================================

    @transaction.atomic
    def commit(self, file_id):

        file = get_object_or_404(
            FileRecord,
            id=file_id,
            owner=self.user,
        )

        if file.upload_state == FileRecord.STATE_COMMITTED:
            return {"status": "already_committed"}, 200

        if file.upload_expires_at and timezone.now() > file.upload_expires_at:
            file.upload_state = FileRecord.STATE_FAILED
            file.save(update_fields=["upload_state"])
            return {"error": "Upload expired"}, 400

        if file.upload_state != FileRecord.STATE_COMPLETED:
            return {"error": "Invalid state for commit"}, 400

        base = r2_base(
            file.tenant_id,
            file.owner_id,
            file.id,
        )

        if not self.storage.exists(f"{base}/manifest.enc"):
            return {"error": "Manifest missing"}, 400

        total_size = self.storage.calculate_total_chunk_size(base)

        success = QuotaService.consume(self.user, total_size)
        if not success:
            return {"error": "Quota exceeded"}, 403

        file.size = total_size
        file.upload_state = FileRecord.STATE_COMMITTED
        file.final_path = base
        file.save(update_fields=["size", "upload_state", "final_path"])

        return {"status": "committed"}, 200