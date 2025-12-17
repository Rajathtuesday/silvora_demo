# files/models.py

import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone


class FileRecord(models.Model):
    """
    Canonical file metadata.
    File contents are ALWAYS encrypted.
    Server never stores plaintext.
    """

    # -------------------------
    # Storage backends
    # -------------------------
    STORAGE_LOCAL = "local"
    STORAGE_R2 = "r2"

    STORAGE_CHOICES = [
        (STORAGE_LOCAL, "Local"),
        (STORAGE_R2, "Cloudflare R2"),
    ]

    # -------------------------
    # Security modes
    # -------------------------
    SECURITY_STANDARD = "standard"
    SECURITY_ZERO = "zero_knowledge"

    SECURITY_CHOICES = [
        (SECURITY_STANDARD, "Standard"),
        (SECURITY_ZERO, "Zero Knowledge"),
    ]

    # -------------------------
    # Identity
    # -------------------------
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    upload_id = models.UUIDField(default=uuid.uuid4, unique=True)

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="files",
    )

    # -------------------------
    # File metadata (non-sensitive)
    # -------------------------
    filename = models.CharField(max_length=255)
    size = models.BigIntegerField(default=0)

    # Encrypted object location (path or R2 object key)
    final_path = models.CharField(max_length=1024, blank=True, default="")

    storage_type = models.CharField(
        max_length=10,
        choices=STORAGE_CHOICES,
        default=STORAGE_LOCAL,
    )

    # 🔐 Security contract (IMMUTABLE per file)
    security_mode = models.CharField(
        max_length=20,
        choices=SECURITY_CHOICES,
        default=SECURITY_STANDARD,
    )

    # Encrypted thumbnail object key (opaque to server)
    thumbnail_key = models.CharField(max_length=512, blank=True)

    # -------------------------
    # Lifecycle
    # -------------------------
    created_at = models.DateTimeField(auto_now_add=True)

    # Soft delete → Trash
    deleted_at = models.DateTimeField(null=True, blank=True)

    # Optional legacy flag (safe to remove later)
    is_completed = models.BooleanField(default=True)

    # -------------------------
    # Helpers
    # -------------------------
    def __str__(self):
        return f"{self.filename} ({self.owner})"

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


class FileChunk(models.Model):
    """
    Tracks uploaded encrypted chunks.
    Used only for resumable uploads.
    """

    file = models.ForeignKey(
        FileRecord,
        on_delete=models.CASCADE,
        related_name="chunks",
    )

    index = models.PositiveIntegerField()
    size = models.PositiveIntegerField()
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("file", "index")
        ordering = ["index"]

    def __str__(self):
        return f"Chunk {self.index} of {self.file.upload_id}"


class FileShare(models.Model):
    """
    FUTURE: Secure file sharing without revealing master keys.

    Stores a file key encrypted for each recipient.
    Server never sees decrypted keys.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    file = models.ForeignKey(
        FileRecord,
        on_delete=models.CASCADE,
        related_name="shared_with",
    )

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="shares_given",
    )

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="shares_received",
    )

    # Encrypted file key (recipient-specific)
    encrypted_file_key = models.BinaryField(null=True, blank=True)

    enc_algo = models.CharField(
        max_length=64,
        default="XCHACHA20_POLY1305",
    )

    key_salt_b64 = models.CharField(max_length=256, null=True, blank=True)
    nonce_b64 = models.CharField(max_length=256, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("file", "recipient")

    def __str__(self):
        return f"{self.file.filename} → {self.recipient.username}"
