# tenants/models.py 
import uuid
from django.db import models


class Tenant(models.Model):

    TYPE_INDIVIDUAL = "INDIVIDUAL"
    TYPE_ORG = "ORG"
    TYPE_WHITELABEL = "WHITELABEL"

    TYPE_CHOICES = [
        (TYPE_INDIVIDUAL, "Individual"),
        (TYPE_ORG, "Organization"),
        (TYPE_WHITELABEL, "White Label"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    name = models.CharField(max_length=255)

    tenant_type = models.CharField(
        max_length=20,
        choices=TYPE_CHOICES,
        default=TYPE_INDIVIDUAL,
    )

    device_limit_per_user = models.IntegerField(default=3)
    storage_limit_bytes = models.BigIntegerField(default=0)  # 0 = unlimited

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.tenant_type})"
    
class TenantQuota(models.Model):
    """
    Storage pool for entire tenant (org or individual).
    """

    tenant = models.OneToOneField(
        "tenants.Tenant",
        on_delete=models.CASCADE,
        related_name="quota",
    )

    limit_bytes = models.BigIntegerField(default=0)  # 0 = unlimited
    used_bytes = models.BigIntegerField(default=0)

    updated_at = models.DateTimeField(auto_now=True)

    def can_store(self, size: int) -> bool:
        if self.limit_bytes == 0:
            return True
        return self.used_bytes + size <= self.limit_bytes

    def consume(self, size: int):
        self.used_bytes += size
        self.save(update_fields=["used_bytes", "updated_at"])

    def release(self, size: int):
        self.used_bytes = max(0, self.used_bytes - size)
        self.save(update_fields=["used_bytes", "updated_at"])