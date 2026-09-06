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

    # Set exactly once, inside UploadService.commit(), at the moment the
    # existing integrity-manifest gate passes. A durable, tamper-evident
    # proof that this file HAD a manifest at commit time -- unlike checking
    # "does integrity.bin exist in storage right now", a compromised server
    # can't rewrite this after the fact without DB access. Never backfilled
    # for rows that predate this field -- doing so would mean re-deriving it
    # from current storage state, exactly the falsifiable signal this field
    # exists to replace.
    integrity_established = models.BooleanField(default=False)

    # --- Rollback/replay hardening (see docs/CRYPTOGRAPHY_SPEC.md) ---
    #
    # The client embeds its own monotonic "version" number *inside* the
    # encrypted integrity manifest (integrity.bin) and enforces it against a
    # per-device trust-on-first-use record (see silvora_app's
    # IntegrityVersionStore). The server can never read that number -- it's
    # inside an AEAD envelope only the client can decrypt -- so the server
    # cannot itself validate "is this version higher than the last one,"
    # and must not pretend to. What the server CAN do without seeing
    # plaintext is fingerprint the *bytes* it stores and refuse to serve
    # back different bytes than the ones it fingerprinted. That is what the
    # three fields below are for: they let download_integrity()/
    # download_manifest() detect a compromised/malicious server (or a
    # storage-only backup restore that reverts these objects without also
    # reverting the DB row) substituting an old-but-validly-signed blob for
    # the current one, closing the gap the existence-only check below
    # (integrity_established) does not: "something is at this key" says
    # nothing about whether it is the SAME something that was there at
    # commit time.
    #
    # Never backfilled for rows that predate these fields, for the same
    # reason integrity_established above is not backfilled: computing a
    # fingerprint from whatever currently sits in storage would just be
    # trusting the exact signal this feature exists to stop trusting blindly.
    # Pre-existing files keep today's existence-only behavior until
    # re-uploaded.

    # How many times store_integrity() has (over)written integrity.bin for
    # this file while the upload was still in flight (never client-supplied,
    # always server-incremented -- so it is trustworthy as an audit signal
    # even though it says nothing about the content itself). Frozen the
    # moment commit() succeeds, since store_integrity() can never run again
    # after that.
    integrity_generation = models.PositiveIntegerField(default=0)

    # SHA-256 (hex) of the integrity.bin bytes as of the last successful
    # store_integrity() call. Opaque to the server either way -- hashing
    # ciphertext requires no key and reveals nothing about the plaintext.
    integrity_sha256 = models.CharField(max_length=64, null=True, blank=True)

    # SHA-256 (hex) of manifest.json's bytes, set once inside commit() at
    # the moment that file is written (manifest.json only exists from
    # commit time onward -- there is no pre-commit rewrite window for it).
    manifest_sha256 = models.CharField(max_length=64, null=True, blank=True)

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
    def mark_deleted(self, retention_days=7):
        now = timezone.now()
        self.deleted_at = now
        self.purge_after = now + timedelta(days=retention_days)
        self.save(update_fields=["deleted_at", "purge_after"])

    def restore_record(self):
        """
        Internal metadata reset. 
        ⚠️ Use QuotaService.restore() for public flow to ensure quota safety.
        """
        self.deleted_at = None
        self.purge_after = None
        self.save(update_fields=["deleted_at", "purge_after"])