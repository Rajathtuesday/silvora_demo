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

    kdf_salt = models.BinaryField()
    kdf_memory_kb = models.PositiveIntegerField()
    kdf_iterations = models.PositiveIntegerField()
    kdf_parallelism = models.PositiveIntegerField()

    key_version = models.PositiveIntegerField(default=1)

    created_at = models.DateTimeField(auto_now_add=True)
    rotated_at = models.DateTimeField(null=True, blank=True)
    
    
class UserQuota(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="quota",
    )

    limit_bytes = models.BigIntegerField(default=1 * 1024 * 1024 * 1024)
    used_bytes = models.BigIntegerField(default=0)

    updated_at = models.DateTimeField(auto_now=True)

    def can_store(self, size: int) -> bool:
        if self.limit_bytes == 0:
            return True
        return self.used_bytes + size <= self.limit_bytes