# files/services/quota_service.py

from django.db import transaction
from users.models import UserQuota
from tenants.models import TenantQuota


class QuotaService:

    @staticmethod
    def get_or_create_user_quota(user):
        quota, _ = UserQuota.objects.get_or_create(
            user=user,
            defaults={"limit_bytes": 1 * 1024 * 1024 * 1024},
        )
        return quota

    @staticmethod
    def get_or_create_tenant_quota(tenant):
        quota, _ = TenantQuota.objects.get_or_create(
            tenant=tenant,
            defaults={"limit_bytes": 0},
        )
        return quota

    @staticmethod
    @transaction.atomic
    def consume(user, size):

        user_quota = UserQuota.objects.select_for_update().get(user=user)
        tenant_quota = TenantQuota.objects.select_for_update().get(
            tenant=user.tenant
        )

        if tenant_quota.limit_bytes != 0:
            if tenant_quota.used_bytes + size > tenant_quota.limit_bytes:
                return False

        if user_quota.limit_bytes != 0:
            if user_quota.used_bytes + size > user_quota.limit_bytes:
                return False

        user_quota.used_bytes += size
        tenant_quota.used_bytes += size

        user_quota.save(update_fields=["used_bytes"])
        tenant_quota.save(update_fields=["used_bytes"])

        return True

    @staticmethod
    @transaction.atomic
    def release(user, size):

        user_quota = UserQuota.objects.select_for_update().get(user=user)
        tenant_quota = TenantQuota.objects.select_for_update().get(
            tenant=user.tenant
        )

        user_quota.used_bytes = max(0, user_quota.used_bytes - size)
        tenant_quota.used_bytes = max(0, tenant_quota.used_bytes - size)

        user_quota.save(update_fields=["used_bytes"])
        tenant_quota.save(update_fields=["used_bytes"])