# files/services/upload_service.py

from datetime import timedelta
from django.utils import timezone
from django.shortcuts import get_object_or_404
from django.db import transaction
from django.core.exceptions import ValidationError

from ..models import FileRecord
from .storage_gateway import StorageGateway
from .quota_service import QuotaService

import hashlib
import json


def r2_base(tenant_id, user_id, file_id):
    return f"Silvora/tenants/{tenant_id}/users/{user_id}/files/{file_id}"


class UploadService:

    def __init__(self, user):
        self.user = user
        self.storage = StorageGateway()

    # ========================================================
    # START UPLOAD
    # ========================================================

    def start(self, data):

        total_size = int(data.get("size", 0))
        security_mode = data.get("security_mode")

        if total_size <= 0:
            return {"error": "Invalid file size"}, 400

        if not self.user.tenant:
            return {"error": "User not assigned to tenant"}, 400

        user_quota = QuotaService.get_or_create_user_quota(self.user)
        QuotaService.get_or_create_tenant_quota(self.user.tenant)

        if not user_quota.can_store(total_size):
            return {"error": "Quota exceeded"}, 403

        expires_at = timezone.now() + timedelta(hours=24)

        file = FileRecord.objects.create(
            owner=self.user,
            tenant=self.user.tenant,
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
    # RESUME
    # ========================================================

    def resume(self, file_id):

        file = get_object_or_404(
            FileRecord,
            id=file_id,
            owner=self.user,
            tenant=self.user.tenant,
        )

        base = r2_base(file.tenant_id, file.owner_id, file.id)
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

        if data is None or len(data) == 0:
            return {"error": "Empty chunk"}, 400

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

        base = r2_base(file.tenant_id, file.owner_id, file.id)

        self.storage.upload_bytes(
            data,
            f"{base}/chunks/chunk_{index}.bin"
        )

        if file.upload_state == FileRecord.UploadState.INITIATED:
            file.upload_state = FileRecord.UploadState.UPLOADING
            file.save(update_fields=["upload_state"])

        return {"stored": True}, 200

    # ========================================================
    # COMMIT (HARDENED)
    # ========================================================

    @transaction.atomic
    def commit(self, file_id):

        file = get_object_or_404(
            FileRecord.objects.select_for_update(),
            id=file_id,
            owner=self.user,
            tenant=self.user.tenant,
        )

        # Idempotency
        if file.upload_state == FileRecord.UploadState.COMMITTED:
            return {"status": "already_committed"}, 200

        # Expiry
        if file.upload_expires_at and timezone.now() > file.upload_expires_at:
            file.upload_state = FileRecord.UploadState.FAILED
            file.save(update_fields=["upload_state"])
            return {"error": "Upload expired"}, 400

        # State validation
        if file.upload_state != FileRecord.UploadState.UPLOADING:
            return {"error": "Invalid state for commit"}, 400

        # 🔐 CRITICAL: Require metadata
        if not file.filename_ciphertext or not file.filename_nonce or not file.filename_mac:
            return {"error": "Filename metadata missing"}, 400

        base = r2_base(file.tenant_id, file.owner_id, file.id)

        chunk_objects = self.storage.list_chunk_objects(base)

        if not chunk_objects:
            return {"error": "No chunks uploaded"}, 400

        manifest_chunks = []
        offset = 0
        total_size = 0

        for index, key, size in chunk_objects:

            data = self.storage.download_bytes(key)

            if data is None or len(data) == 0:
                return {"error": f"Corrupted chunk {index}"}, 400

            sha256_hex = hashlib.sha256(data).hexdigest()

            manifest_chunks.append({
                "i": index,
                "o": offset,
                "s": size,
                "sha256": sha256_hex,
            })

            offset += size
            total_size += size

        if total_size <= 0:
            return {"error": "Invalid total size"}, 400

        manifest = {
            "v": 1,
            "key_version": file.key_version,
            "total_chunks": len(manifest_chunks),
            "size": total_size,
            "chunks": manifest_chunks,
        }

        manifest_bytes = json.dumps(
            manifest,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()

        manifest_key = f"{base}/manifest.json"

        self.storage.upload_bytes(manifest_bytes, manifest_key)

        # Quota consume AFTER manifest built
        success = QuotaService.consume(self.user, total_size)

        if not success:
            return {"error": "Quota exceeded"}, 403

        file.size = total_size
        file.upload_state = FileRecord.UploadState.COMMITTED
        file.final_path = base
        file.manifest_path = manifest_key

        file.save(update_fields=[
            "size",
            "upload_state",
            "final_path",
            "manifest_path",
        ])

        return {"status": "committed"}, 200