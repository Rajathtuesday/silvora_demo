# files/models.py

import uuid
from datetime import timedelta
from django.conf import settings
from django.db import models
from django.utils import timezone


# files/models.py

class FileRecord(models.Model):

    class UploadState(models.TextChoices):
        INITIATED = "initiated"
        UPLOADING = "uploading"
        COMPLETED = "completed"
        COMMITTED = "committed"
        FAILED = "failed"

    STORAGE_LOCAL = "local"
    STORAGE_R2 = "r2"

    SECURITY_STANDARD = "standard"
    SECURITY_ZERO = "zero_knowledge"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    upload_id = models.UUIDField(default=uuid.uuid4, unique=True)

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="files",
    )

    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.CASCADE,
        related_name="files",
    )

    # 🔐 ENCRYPTED FILENAME
    filename_ciphertext = models.BinaryField(null=True, blank=True)
    filename_nonce = models.BinaryField(null=True, blank=True)
    filename_mac = models.BinaryField(null=True, blank=True)

    size = models.BigIntegerField(default=0)

    final_path = models.CharField(max_length=1024, null=True, blank=True)
    manifest_path = models.CharField(max_length=1024, null=True, blank=True)

    storage_type = models.CharField(
        max_length=10,
        default=STORAGE_R2,
    )

    security_mode = models.CharField(
        max_length=20,
    )

    key_version = models.PositiveIntegerField(default=1)

    created_at = models.DateTimeField(auto_now_add=True)

    deleted_at = models.DateTimeField(null=True, blank=True)
    purge_after = models.DateTimeField(null=True, blank=True)

    upload_state = models.CharField(
        max_length=20,
        choices=UploadState.choices,
        default=UploadState.INITIATED,
    )

    upload_expires_at = models.DateTimeField(null=True, blank=True)

    @property
    def is_deleted(self):
        return self.deleted_at is not None
    def mark_deleted(self, retention_days=30):
        now = timezone.now()
        self.deleted_at = now
        self.purge_after = now + timedelta(days=retention_days)
        self.save(update_fields=["deleted_at", "purge_after"])

    def restore(self):
        self.deleted_at = None
        self.purge_after = None
        self.save(update_fields=["deleted_at", "purge_after"])