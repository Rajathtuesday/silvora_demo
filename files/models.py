# files/models.py
# files/models.py
import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone


class FileRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    upload_id = models.UUIDField(default=uuid.uuid4, unique=True)

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="files",
    )

    filename = models.CharField(max_length=255)
    size = models.BigIntegerField(default=0)
    final_path = models.CharField(max_length=1024, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)

    # 🔥 NEW: soft delete timestamp (Trash)
    deleted_at = models.DateTimeField(null=True, blank=True)

    # Optional: if you still use it elsewhere
    is_completed = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.filename} ({self.owner})"

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None



class FileShare(models.Model):
    """
    FUTURE: Enables secure file sharing without revealing master keys.
    We'll store an encrypted file key per-recipient here.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    file = models.ForeignKey(FileRecord, on_delete=models.CASCADE, related_name="shared_with")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="shares_given")
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="shares_received")

    encrypted_file_key = models.BinaryField(null=True, blank=True)
    enc_algo = models.CharField(max_length=64, default="XCHACHA20_POLY1305")
    key_salt_b64 = models.CharField(max_length=256, null=True, blank=True)
    nonce_b64 = models.CharField(max_length=256, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("file", "recipient")

    def __str__(self):
        return f"{self.file.filename} → {self.recipient.username}"
    

# files/models.py
class FileChunk(models.Model):
    file = models.ForeignKey(FileRecord, on_delete=models.CASCADE, related_name="chunks")
    index = models.PositiveIntegerField()
    size = models.PositiveIntegerField()
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("file", "index")
        ordering = ["index"]