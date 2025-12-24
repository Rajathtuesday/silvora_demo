# files/models.py

import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone


class FileRecord(models.Model):
    """
    Canonical encrypted file metadata.
    Server never sees plaintext.
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
    # Non-sensitive metadata
    # -------------------------
    filename = models.CharField(max_length=255)
    size = models.BigIntegerField(default=0)

    # Opaque encrypted object location
    final_path = models.CharField(max_length=1024)
    #encrypted manifest location(R2 key or local path )
    manifest_path = models.CharField(max_length=1024)

    storage_type = models.CharField(
        max_length=10,
        choices=STORAGE_CHOICES,
        default=STORAGE_LOCAL,
    )

    # 🔐 Security contract (IMMUTABLE)
    security_mode = models.CharField(
        max_length=20,
        choices=SECURITY_CHOICES,
    )

    # 🔑 Which master key version encrypted this file
    key_version = models.PositiveIntegerField(default=1)

    # Encrypted thumbnail reference (opaque)
    thumbnail_key = models.CharField(max_length=512, blank=True)

    # -------------------------
    # Lifecycle
    # -------------------------
    created_at = models.DateTimeField(auto_now_add=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.filename} ({self.owner_id})"

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


# ============================================================
# MASTER KEY ENVELOPE
# ============================================================

class MasterKeyEnvelope(models.Model):
    """
    Stores the user's encrypted master key.
    Server never sees plaintext or derived keys.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    owner = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="master_key_envelope",
    )

    # 🔐 Password-encrypted master key
    enc_master_key_pwd = models.BinaryField()

    # 🔑 Argon2id parameters
    kdf_salt = models.BinaryField()
    kdf_memory_kb = models.PositiveIntegerField()
    kdf_iterations = models.PositiveIntegerField()
    kdf_parallelism = models.PositiveIntegerField()

    # 🔄 Rotation tracking
    key_version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    rotated_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"MasterKeyEnvelope(user={self.owner_id}, v={self.key_version})"


# ============================================================
# FUTURE: SECURE SHARING
# ============================================================

class FileShare(models.Model):
    """
    Stores per-recipient encrypted file keys.
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

    encrypted_file_key = models.BinaryField()

    enc_algo = models.CharField(
        max_length=64,
        default="XCHACHA20_POLY1305",
    )

    key_salt_b64 = models.CharField(max_length=256)
    nonce_b64 = models.CharField(max_length=256)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("file", "recipient")

    def __str__(self):
        return f"{self.file.filename} → {self.recipient_id}"
