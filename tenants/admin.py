# tenants/admin.py

from django.contrib import admin
from .models import Tenant, TenantQuota


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):

    list_display = (
        "id",
        "name",
        "tenant_type",
        "created_at",
    )

    list_filter = (
        "tenant_type",
    )

    search_fields = (
        "name",
        "id",
    )

    readonly_fields = (
        "id",
        "created_at",
    )


@admin.register(TenantQuota)
class TenantQuotaAdmin(admin.ModelAdmin):

    list_display = (
        "tenant",
        "limit_bytes",
        "used_bytes",
    )

    search_fields = (
        "tenant__name",
    )