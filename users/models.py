# users/models.py

import uuid
from django.db import models
from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    tenant = models.ForeignKey(
        "tenants.Tenant",
        on_delete=models.CASCADE,
        related_name="users",
        null=True,
        blank=True,
    )

    # Non-blocking: an unverified user can still log in and use the vault.
    # Recovery already works without email (the 24-word phrase is the real
    # safety net) — gating access on a verification email arriving would risk
    # locking someone out of their own encrypted files over a deliverability
    # hiccup, which is a worse failure than an unverified address.
    email_verified = models.BooleanField(default=False)
    email_verified_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.email or self.username

class MasterKeyEnvelope(models.Model):

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="master_key_envelope",
    )

    enc_master_key = models.BinaryField()
    enc_master_key_nonce = models.BinaryField()

    kdf_salt = models.BinaryField()
    kdf_memory_kb = models.PositiveIntegerField()
    kdf_iterations = models.PositiveIntegerField()
    kdf_parallelism = models.PositiveIntegerField()

    # ----- Recovery phrase: a second, independent wrapping of the master key -----
    # The master key is also encrypted under a KEK derived from a 24-word recovery
    # phrase. Nullable so pre-recovery accounts and migrations stay valid.
    enc_master_key_recovery = models.BinaryField(null=True, blank=True)
    enc_master_key_recovery_nonce = models.BinaryField(null=True, blank=True)
    recovery_kdf_salt = models.BinaryField(null=True, blank=True)
    recovery_kdf_memory_kb = models.PositiveIntegerField(null=True, blank=True)
    recovery_kdf_iterations = models.PositiveIntegerField(null=True, blank=True)
    recovery_kdf_parallelism = models.PositiveIntegerField(null=True, blank=True)

    # Hash of the recovery-auth-key (also derived from the phrase). Lets the
    # server verify the user really holds the phrase during a logged-out reset,
    # without ever learning the phrase. Stored via Django's password hashers.
    recovery_auth_hash = models.CharField(max_length=255, null=True, blank=True)

    key_version = models.PositiveIntegerField(default=1)

    created_at = models.DateTimeField(auto_now_add=True)
    rotated_at = models.DateTimeField(null=True, blank=True)
    
    
class SubscriptionTier(models.TextChoices):
    FREE = 'free', 'Free (1GB)'
    PRO = 'pro', 'Pro (100GB)'
    ENTERPRISE = 'enterprise', 'Enterprise (1TB)'

class UserQuota(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="quota",
    )
    
    tier = models.CharField(
        max_length=20,
        choices=SubscriptionTier.choices,
        default=SubscriptionTier.FREE
    )

    limit_bytes = models.BigIntegerField(default=1 * 1024 * 1024 * 1024)
    used_bytes = models.BigIntegerField(default=0)

    updated_at = models.DateTimeField(auto_now=True)

    def can_store(self, size: int) -> bool:
        if self.limit_bytes == 0:
            return True
        return self.used_bytes + size <= self.limit_bytes

    def set_tier(self, new_tier):
        self.tier = new_tier
        if new_tier == SubscriptionTier.FREE:
            self.limit_bytes = 1 * 1024 * 1024 * 1024
        elif new_tier == SubscriptionTier.PRO:
            self.limit_bytes = 100 * 1024 * 1024 * 1024
        elif new_tier == SubscriptionTier.ENTERPRISE:
            self.limit_bytes = 1024 * 1024 * 1024 * 1024
        self.save(update_fields=['tier', 'limit_bytes'])