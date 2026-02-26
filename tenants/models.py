# tenants/models.py

import uuid
from django.db import models


class Tenant(models.Model):

    TYPE_INDIVIDUAL = "INDIVIDUAL"
    TYPE_ORG = "ORG"

    TYPE_CHOICES = [
        (TYPE_INDIVIDUAL, "Individual"),
        (TYPE_ORG, "Organization"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    name = models.CharField(max_length=255)

    tenant_type = models.CharField(
        max_length=20,
        choices=TYPE_CHOICES,
        default=TYPE_INDIVIDUAL,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.tenant_type})"


class TenantQuota(models.Model):

    tenant = models.OneToOneField(
        Tenant,
        on_delete=models.CASCADE,
        related_name="quota",
    )

    limit_bytes = models.BigIntegerField(default=0)  # 0 = unlimited
    used_bytes = models.BigIntegerField(default=0)

    def can_store(self, size):
        if self.limit_bytes == 0:
            return True
        return self.used_bytes + size <= self.limit_bytes