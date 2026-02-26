# tenants/signals.py
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Tenant, TenantQuota


@receiver(post_save, sender=Tenant)
def create_quota_for_tenant(sender, instance, created, **kwargs):
    if created:
        TenantQuota.objects.create(
            tenant=instance,
            limit_bytes=0,  # 0 = unlimited default
            used_bytes=0,
        )