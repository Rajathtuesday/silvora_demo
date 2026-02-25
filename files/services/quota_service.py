from django.db import transaction
from ..models import UserQuota
from tenants.models import TenantQuota, Tenant


class QuotaService:

    @staticmethod
    def get_or_create(user):
        quota, _ = UserQuota.objects.get_or_create(
            user=user,
            defaults={"limit_bytes": 1 * 1024 * 1024 * 1024},
        )
        return quota

    # ==================================================
    # HIERARCHICAL CONSUME (Tenant-aware)
    # ==================================================

    @staticmethod
    @transaction.atomic
    def consume(user, size):

        user_quota = UserQuota.objects.select_for_update().get(user=user)
        tenant = user.profile.tenant
        tenant_quota = TenantQuota.objects.select_for_update().get(
            tenant=tenant
        )

        # ----------------------------------------------
        # INDIVIDUAL TENANT
        # ----------------------------------------------
        if tenant.tenant_type == Tenant.TYPE_INDIVIDUAL:

            # Check user cap
            if user_quota.limit_bytes != 0:
                if user_quota.used_bytes + size > user_quota.limit_bytes:
                    return False

            # Check tenant cap
            if tenant_quota.limit_bytes != 0:
                if tenant_quota.used_bytes + size > tenant_quota.limit_bytes:
                    return False

        # ----------------------------------------------
        # ORG / WHITELABEL
        # ----------------------------------------------
        else:

            # Only enforce tenant pool
            if tenant_quota.limit_bytes != 0:
                if tenant_quota.used_bytes + size > tenant_quota.limit_bytes:
                    return False

        # ----------------------------------------------
        # CONSUME
        # ----------------------------------------------
        user_quota.used_bytes += size
        tenant_quota.used_bytes += size

        user_quota.save(update_fields=["used_bytes"])
        tenant_quota.save(update_fields=["used_bytes"])

        return True

    # ==================================================
    # RELEASE
    # ==================================================

    @staticmethod
    @transaction.atomic
    def release(user, size):

        user_quota = UserQuota.objects.select_for_update().get(user=user)
        tenant_quota = TenantQuota.objects.select_for_update().get(
            tenant=user.profile.tenant
        )

        user_quota.used_bytes = max(0, user_quota.used_bytes - size)
        tenant_quota.used_bytes = max(0, tenant_quota.used_bytes - size)

        user_quota.save(update_fields=["used_bytes"])
        tenant_quota.save(update_fields=["used_bytes"])