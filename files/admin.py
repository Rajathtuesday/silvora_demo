# files/admin.py

from django.contrib import admin
from .models import FileRecord


@admin.register(FileRecord)
class FileRecordAdmin(admin.ModelAdmin):

    list_display = (
        "id",
        "owner",
        "tenant",
        "size",
        "upload_state",
        "security_mode",
        "integrity_established",
        "created_at",
        "deleted_at",
    )

    list_filter = (
        "tenant",
        "upload_state",
        "security_mode",
        "deleted_at",
    )

    search_fields = (
        "owner__email",
        "tenant__name",
        "id",
    )

    readonly_fields = (
        "id",
        "created_at",
        "deleted_at",
        "upload_state",
        "size",
        "integrity_established",
        "integrity_generation",
        "integrity_sha256",
        "manifest_sha256",
    )

    ordering = ("-created_at",)