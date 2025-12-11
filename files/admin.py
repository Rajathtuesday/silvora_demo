# files/admin.py
from django.contrib import admin
from .models import FileRecord

@admin.register(FileRecord)
class FileRecordAdmin(admin.ModelAdmin):
    list_display = ('id', 'filename', 'size', 'created_at', 'upload_id')
    readonly_fields = ('created_at',)
